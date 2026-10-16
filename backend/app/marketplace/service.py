"""Marketplace catalog service — list / detail / version reads.

Slice A focus: read-side only. Install/publish moves into separate
``install_service`` and ``publish_service`` modules (later slices).

Routers consume:

* ``list_items(db, user, filters)``    — paginated catalog
* ``get_item(db, user, item_id)``      — detail. 404 collapsing for
                                          enumeration-oracle safety.
* ``list_versions(db, user, item_id)`` — version history
* ``get_version(db, user, version_id)``— version detail
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.authz.catalog_scope import (
    catalog_view_candidate_predicate,
    installation_child_scope_predicate,
)
from app.authz.catalog_version_scope import load_item_version
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.error_codes import marketplace_item_not_found
from app.marketplace.access import (
    ItemAuthorizer,
    ListGate,
    can_install_item,
    can_view_item,
    is_owner,
)
from app.marketplace.origin_service import (
    bulk_derive_installation_summaries,
    derive_credential_summary,
    derive_installation_summary,
)
from app.marketplace.schemas import (
    CredentialSummaryOut,
    MarketplaceInstallationSummary,
    MarketplaceItemListFilters,
    MarketplaceItemOut,
    MarketplaceItemsPage,
    MarketplaceVersionDetail,
    MarketplaceVersionSummary,
    ResourcePublicationSummaryOut,
)
from app.models.marketplace import (
    MarketplaceItem,
    MarketplaceItemACL,
    MarketplacePublicationLink,
    MarketplaceVersion,
)

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


# ---------------------------------------------------------------------------
# Catalog query construction
# ---------------------------------------------------------------------------


def _catalog_owner_predicate(user: CurrentUser) -> ColumnElement[bool]:
    """Use the same existing owner visibility clause for detail and listing."""
    return MarketplaceItem.owner_user_id == user.id


def _catalog_view_stmt(
    user: CurrentUser, context: OrgContext, gate: ListGate | None = None
) -> Select[tuple[MarketplaceItem]]:
    """Match persisted visibility before any authorized read can flush staged edits."""
    if gate is not None and gate.enforced:  # D-05: candidates, no operator bypass
        return select(MarketplaceItem).where(gate.view_predicate())
    return select(MarketplaceItem).where(
        catalog_view_candidate_predicate(context),
        or_(
            _catalog_owner_predicate(user),
            literal(user.is_super_user),
            and_(MarketplaceItem.status != "disabled", MarketplaceItem.visibility == "system"),
            and_(
                MarketplaceItem.status == "published",
                MarketplaceItem.visibility.in_(("public", "unlisted", "restricted")),
            ),
        ),
    )


def _base_catalog_query(
    user: CurrentUser,
    context: OrgContext,
    *,
    public_listed_only: bool,
    gate: ListGate | None = None,
):
    """Build a SELECT that respects the user's visibility scope (Spec §10.1).

    super_user      → all items, no default visibility gate.
    regular user    → owner-of OR system OR (public+published+listed)
                       OR restricted-with-ACL row.

    ``public_listed_only`` controls the public path:

    * ``True`` (default for catalog list) — only ``is_listed=True`` public
      items appear. Unlisted public items + community/moderation queue
      items are excluded so the catalog surface matches the listed view.
    * ``False`` — caller passed ``?is_listed=true|false`` explicitly; the
      apply_filters layer takes over so the explicit value (True OR False)
      is honored verbatim. Used by super_user moderation views to surface
      the ``is_listed=False`` queue.

    ``visibility='unlisted'`` is **always excluded** from list responses
    (Spec §7 — "direct-link only"). Detail (``get_item``) keeps unlisted
    access by ID + ACL-or-owner ungated.

    The ACL test uses a correlated EXISTS to keep the result row count
    equal to the items count (1:N → existence join would duplicate).
    """

    stmt = _catalog_view_stmt(user, context, gate)
    if gate is not None and gate.enforced:
        return stmt.where(gate.list_predicate(public_listed_only=public_listed_only))
    if user.is_super_user:
        return stmt

    acl_exists = (
        select(MarketplaceItemACL.item_id)
        .where(
            MarketplaceItemACL.item_id == MarketplaceItem.id,
            MarketplaceItemACL.user_id == user.id,
        )
        .exists()
    )

    public_path = and_(
        MarketplaceItem.status == "published",
        MarketplaceItem.visibility == "public",
    )
    if public_listed_only:
        # Default — only listed public items count toward catalog visibility.
        public_path = and_(public_path, MarketplaceItem.is_listed.is_(True))

    visibility_clauses = [
        _catalog_owner_predicate(user),
        MarketplaceItem.visibility == "system",
        public_path,
        and_(
            MarketplaceItem.status == "published",
            MarketplaceItem.visibility == "restricted",
            acl_exists,
        ),
    ]

    return stmt.where(or_(*visibility_clauses))


def _installed_for_user_exists(context: OrgContext):
    """User 의 active installation 이 있는 item id 를 EXISTS 로 잡는다.

    SQL 단계에서 적용해야 ``installed=true`` filter + pagination 이 정확하다.
    post-load 후처리로 두면 ``limit`` 가 base catalog 결과에 먼저 적용되어
    user installation 이 있는 item 이 첫 페이지 밖으로 밀려나면 결과에서
    누락된다.
    """

    from app.models.marketplace import MarketplaceInstallation

    return (
        select(MarketplaceInstallation.id)
        .where(
            MarketplaceInstallation.item_id == MarketplaceItem.id,
            private_scope_predicate(MarketplaceInstallation, context),
            installation_child_scope_predicate(context),
            MarketplaceInstallation.install_status != "uninstalled",
        )
        .exists()
    )


def _apply_filters(stmt, filters: MarketplaceItemListFilters):
    if filters.resource_type:
        stmt = stmt.where(MarketplaceItem.resource_type == filters.resource_type)
    if filters.q:
        # Case-insensitive substring across name/description. ILIKE on
        # Postgres / LIKE on SQLite (case-insensitive by default).
        needle = f"%{filters.q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(MarketplaceItem.name).like(needle),
                func.lower(MarketplaceItem.description).like(needle),
            )
        )
    if filters.visibility:
        stmt = stmt.where(MarketplaceItem.visibility.in_(filters.visibility))
    if filters.is_listed is not None:
        stmt = stmt.where(MarketplaceItem.is_listed == filters.is_listed)
    if filters.source_kind:
        stmt = stmt.where(MarketplaceItem.source_kind == filters.source_kind)
    # Note: category/installed/install_state/support_level filtered post-load
    # (categories is a JSON column; installed needs a join we materialize
    # after the page is fetched to keep the SQL simple).
    return stmt


def _catalog_stmt(
    user: CurrentUser,
    filters: MarketplaceItemListFilters,
    context: OrgContext,
    gate: ListGate | None = None,
):
    public_listed_only = filters.is_listed is None
    stmt = _base_catalog_query(user, context, public_listed_only=public_listed_only, gate=gate)
    stmt = _apply_filters(stmt, filters)
    if filters.installed is not None:
        exists_clause = _installed_for_user_exists(context)
        stmt = stmt.where(exists_clause if filters.installed else ~exists_clause)
    return stmt


def _catalog_rows_stmt(
    user: CurrentUser,
    filters: MarketplaceItemListFilters,
    context: OrgContext,
    gate: ListGate | None = None,
):
    return (
        _catalog_stmt(user, filters, context, gate)
        .options(
            selectinload(MarketplaceItem.latest_version),
            selectinload(MarketplaceItem.acl_entries),
        )
        .order_by(
            MarketplaceItem.is_listed.desc(),
            MarketplaceItem.published_at.desc().nullslast(),
            MarketplaceItem.created_at.desc(),
        )
        .execution_options(populate_existing=True)
    )


def _has_post_load_filters(
    filters: MarketplaceItemListFilters, gate: ListGate | None = None
) -> bool:
    # Enforced pages are confirmed row by row (BatchCheck), so they paginate like
    # post-load filters.
    enforced = gate is not None and gate.enforced
    return bool(filters.install_state or filters.support_level or filters.category or enforced)


async def _fetch_catalog_rows(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    filters: MarketplaceItemListFilters,
    limit: int,
    offset: int,
    gate: ListGate | None = None,
) -> list[MarketplaceItem]:
    candidate = (
        _catalog_view_stmt(user, context, gate)
        .with_only_columns(MarketplaceItem.id)
        .limit(1)
        .execution_options(autoflush=False)
    )
    if await db.scalar(candidate) is None:
        return []
    stmt = _catalog_rows_stmt(user, filters, context, gate).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().unique().all())


async def _apply_post_load_filters(
    db: AsyncSession,
    *,
    rows: Sequence[MarketplaceItem],
    user: CurrentUser,
    context: OrgContext,
    filters: MarketplaceItemListFilters,
    gate: ListGate | None = None,
) -> list[MarketplaceItem]:
    filtered = list(rows)

    # ``installed`` 는 위에서 이미 SQL 로 처리됨. ``install_state`` (active /
    # needs_setup / disabled) 는 derive_installation_summary 가 결정하는
    # 동적 상태(예: credential gap 으로 active → needs_setup 승급) 이므로
    # 여전히 post-load 단계에서만 정확히 적용 가능.
    if filters.install_state:
        installation_summaries = await bulk_derive_installation_summaries(
            db, items=filtered, context=context
        )
        kept = []
        for item in filtered:
            summary = installation_summaries.get(
                item.id,
                MarketplaceInstallationSummary(installed=False),
            )
            if summary.status == filters.install_state:
                kept.append(item)
        filtered = kept
    if filters.support_level:
        filtered = [
            i
            for i in filtered
            if i.latest_version is not None
            and (i.latest_version.execution_profile or {}).get("support_level")
            == filters.support_level
        ]
    if filters.category:
        filtered = [
            i
            for i in filtered
            if isinstance(i.categories, list) and any(c in i.categories for c in filters.category)
        ]
    if gate is not None:  # shadow: sample and keep; enforce: OpenFGA can_view
        filtered = await gate.filter(filtered)

    return filtered


# ---------------------------------------------------------------------------
# Item → response projection
# ---------------------------------------------------------------------------


async def _project_item(
    db: AsyncSession,
    item: MarketplaceItem,
    user: CurrentUser,
    context: OrgContext,
    *,
    installation_summary: MarketplaceInstallationSummary | None = None,
    has_publication_link: bool | None = None,
) -> MarketplaceItemOut:
    latest_version_summary: MarketplaceVersionSummary | None = None
    if item.latest_version_id is not None:
        # ``latest_version`` may be eager-loaded; fall back to a lookup.
        lv = item.latest_version
        if lv is None:
            lv = await load_item_version(db, item.id, item.latest_version_id)
        if lv is not None:
            latest_version_summary = MarketplaceVersionSummary.model_validate(lv)

    requirements = (
        item.latest_version.credential_requirements if item.latest_version is not None else None
    )
    credential_summary: CredentialSummaryOut = derive_credential_summary(requirements)

    installation: MarketplaceInstallationSummary = installation_summary or (
        await derive_installation_summary(db, item=item, context=context)
    )

    # publication_summary는 viewer 본인이 publish한 자기 리소스인지 보여주는 정보.
    # owner 일치만으로 published 상태를 그리면 — 사용자가 source skill을 삭제한
    # 직후에도 marketplace_item.owner_user_id는 남아있어 카탈로그가 "Manage"
    # CTA를 노출. 그 결과 자기 publish 백업본을 다시 install 불가능. PRD §6
    # 정신상 source resource를 잃은 owner는 marketplace 원본에서 재install이
    # 합리적이므로, publication_link 존재 여부까지 함께 확인해야 한다.
    owner_view = is_owner(item, user) and (item.org_id, item.tenant_id) == (
        context.org_id,
        context.tenant_id,
    )
    if has_publication_link is None:
        has_publication_link = False
        if owner_view:
            link_exists = await db.execute(
                select(MarketplacePublicationLink.id)
                .where(MarketplacePublicationLink.item_id == item.id)
                .limit(1)
            )
            has_publication_link = link_exists.scalar_one_or_none() is not None

    publication_visible = owner_view and has_publication_link

    publication = ResourcePublicationSummaryOut.model_validate(
        {
            "state": (
                _publication_state_for_owner(item) if publication_visible else "not_published"
            ),
            "item_id": item.id if publication_visible else None,
            "visibility": item.visibility if publication_visible else None,
            "status": item.status if publication_visible else None,
            "is_listed": item.is_listed if publication_visible else False,
            "latest_version_id": item.latest_version_id if publication_visible else None,
            "version_number": (
                item.latest_version.version_number
                if publication_visible and item.latest_version is not None
                else None
            ),
            "shared_user_count": len(item.acl_entries) if publication_visible else 0,
        }
    )

    return MarketplaceItemOut(
        id=item.id,
        resource_type=item.resource_type,  # type: ignore[arg-type]
        name=item.name,
        slug=item.slug,
        description=item.description,
        icon_id=item.icon_id,
        icon_url=item.icon_url,
        visibility=item.visibility,  # type: ignore[arg-type]
        status=item.status,  # type: ignore[arg-type]
        is_system=item.is_system,
        is_listed=item.is_listed,
        tags=item.tags,
        categories=item.categories,
        locale=item.locale,
        created_at=item.created_at,
        updated_at=item.updated_at,
        published_at=item.published_at,
        latest_version=latest_version_summary,
        credential_summary=credential_summary,
        execution_profile=(
            item.latest_version.execution_profile if item.latest_version is not None else None
        ),
        origin_summary=None,
        publication_summary=publication,
        installation=installation,
        # owner / super_user 만 ACL user_id 목록을 본다. ResourcePublicationSummaryOut.
        # shared_user_count 는 모든 viewer 에게 노출되지만 user id 자체는 leak
        # 하지 않는다 — 다른 user 가 본인이 ACL 에 있는지 알 수 있게 되어
        # information leak 발생.
        acl_user_ids=(
            [a.user_id for a in (item.acl_entries or [])]
            if (
                owner_view
                or (
                    user.is_super_user
                    and item.is_system
                    and item.org_id is None
                    and item.tenant_id is None
                )
            )
            else None
        ),
    )


async def _bulk_publication_link_item_ids(
    db: AsyncSession, items: Sequence[MarketplaceItem], user: CurrentUser, *, context: OrgContext
) -> set[uuid.UUID]:
    owner_item_ids = [
        item.id
        for item in items
        if is_owner(item, user)
        and (item.org_id, item.tenant_id) == (context.org_id, context.tenant_id)
    ]
    if not owner_item_ids:
        return set()
    rows = (
        (
            await db.execute(
                select(MarketplacePublicationLink.item_id).where(
                    MarketplacePublicationLink.item_id.in_(owner_item_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def _project_items(
    db: AsyncSession,
    items: Sequence[MarketplaceItem],
    user: CurrentUser,
    context: OrgContext,
) -> list[MarketplaceItemOut]:
    installation_summaries = await bulk_derive_installation_summaries(
        db, items=items, context=context
    )
    publication_link_item_ids = await _bulk_publication_link_item_ids(
        db, items, user, context=context
    )
    return [
        await _project_item(
            db,
            item,
            user,
            installation_summary=installation_summaries.get(item.id),
            has_publication_link=item.id in publication_link_item_ids,
            context=context,
        )
        for item in items
    ]


def _publication_state_for_owner(item: MarketplaceItem) -> str:
    if item.status == "draft":
        return "draft"
    if item.status == "disabled":
        return "disabled"
    if item.status == "published":
        if item.visibility == "private":
            return "published_private"
        if item.visibility == "restricted":
            return "published_restricted"
        if item.visibility == "public":
            return "published_public_listed" if item.is_listed else "published_public_unlisted"
        if item.visibility == "unlisted":
            return "published_unlisted"
    return "not_published"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_items(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    filters: MarketplaceItemListFilters,
    limit: int = 50,
    offset: int = 0,
    gate: ListGate | None = None,
) -> Sequence[MarketplaceItemOut]:
    await require_private_actor(db, context)
    rows = await _fetch_catalog_rows(
        db, user=user, filters=filters, limit=limit, offset=offset, context=context, gate=gate
    )
    rows = await _apply_post_load_filters(
        db, rows=rows, user=user, filters=filters, context=context, gate=gate
    )
    return await _project_items(db, rows, user, context=context)


async def _count_catalog_items(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    filters: MarketplaceItemListFilters,
    gate: ListGate | None = None,
) -> int | None:
    if _has_post_load_filters(filters, gate):
        return None
    stmt = _catalog_stmt(user, filters, context, gate)
    return await db.scalar(
        select(func.count()).select_from(stmt.subquery()).execution_options(autoflush=False)
    )


async def _scan_post_filtered_page(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    filters: MarketplaceItemListFilters,
    limit: int,
    offset: int,
    gate: ListGate | None = None,
) -> tuple[list[MarketplaceItem], bool, int | None]:
    batch_size = max(limit * 3, 50)
    raw_offset = 0
    filtered_seen = 0
    page_rows: list[MarketplaceItem] = []
    has_more = False
    exhausted = False

    while True:
        raw_rows = await _fetch_catalog_rows(
            db,
            user=user,
            filters=filters,
            limit=batch_size,
            offset=raw_offset,
            context=context,
            gate=gate,
        )
        if not raw_rows:
            exhausted = True
            break

        filtered_rows = await _apply_post_load_filters(
            db, rows=raw_rows, user=user, filters=filters, context=context, gate=gate
        )
        for item in filtered_rows:
            if filtered_seen < offset:
                filtered_seen += 1
                continue
            if len(page_rows) < limit:
                page_rows.append(item)
                filtered_seen += 1
                continue
            has_more = True
            break

        if has_more:
            break
        if len(raw_rows) < batch_size:
            exhausted = True
            break
        raw_offset += batch_size

    return page_rows, has_more, filtered_seen if exhausted else None


async def list_items_page(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    filters: MarketplaceItemListFilters,
    limit: int = 50,
    offset: int = 0,
    gate: ListGate | None = None,
) -> MarketplaceItemsPage:
    await require_private_actor(db, context)
    if _has_post_load_filters(filters, gate):
        rows, has_more, total = await _scan_post_filtered_page(
            db, user=user, filters=filters, limit=limit, offset=offset, context=context, gate=gate
        )
    else:
        raw_rows = await _fetch_catalog_rows(
            db, user=user, filters=filters, limit=limit + 1, offset=offset, context=context
        )
        has_more = len(raw_rows) > limit
        rows = raw_rows[:limit]
        if gate is not None:  # shadow sample of the page (legacy rows kept)
            rows = await gate.filter(rows)
        total = await _count_catalog_items(db, user=user, filters=filters, context=context)

    return MarketplaceItemsPage(
        items=await _project_items(db, rows, user, context=context),
        limit=limit,
        offset=offset,
        total=total,
        has_more=has_more,
        next_offset=offset + limit if has_more else None,
    )


async def get_item(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    item_id: uuid.UUID,
    authorize: ItemAuthorizer | None = None,
) -> MarketplaceItem | None:
    """Fetch an item if the user is allowed to see it.

    Returns ``None`` for both "doesn't exist" and "exists but hidden" so
    callers can map both to a single 404 (enumeration oracle safety).
    With ``authorize`` (shadow/enforce, D-05) a hidden item raises the 404 instead.
    """

    if authorize is not None and authorize.enforced:
        return await authorize(item_id, None)
    found = await _legacy_get_item(db, user=user, context=context, item_id=item_id)
    if authorize is not None:
        return await authorize(item_id, found)
    return found


async def _legacy_get_item(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    item_id: uuid.UUID,
) -> MarketplaceItem | None:
    await require_private_actor(db, context)
    candidate = (
        _catalog_view_stmt(user, context)
        .with_only_columns(MarketplaceItem.id)
        .where(MarketplaceItem.id == item_id)
        .execution_options(autoflush=False)
    )
    if await db.scalar(candidate) is None:
        return None
    stmt = (
        _catalog_view_stmt(user, context)
        .where(MarketplaceItem.id == item_id)
        .options(
            selectinload(MarketplaceItem.latest_version),
            selectinload(MarketplaceItem.acl_entries),
        )
        # Publish/update routes may have loaded this item earlier in the same
        # session. Refresh relationships so the response cannot project the
        # previous latest_version from the identity map.
        .execution_options(populate_existing=True)
    )
    item = (await db.execute(stmt)).scalar_one_or_none()
    if item is None:
        return None
    if not can_view_item(item, user):
        return None
    return item


async def project_item(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    user: CurrentUser,
    context: OrgContext,
    authorize: ItemAuthorizer | None = None,
) -> MarketplaceItemOut:
    """Public projection helper (router uses after ``get_item``)."""

    await require_private_actor(db, context)
    current = await get_item(db, user=user, context=context, item_id=item.id, authorize=authorize)
    if current is None:
        raise marketplace_item_not_found()
    with db.no_autoflush:
        return await _project_item(db, current, user, context=context)


async def list_versions(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    item_id: uuid.UUID,
    authorize: ItemAuthorizer | None = None,
) -> Sequence[MarketplaceVersionSummary] | None:
    item = await get_item(db, user=user, item_id=item_id, context=context, authorize=authorize)
    if item is None:
        return None
    stmt = (
        select(MarketplaceVersion)
        .where(MarketplaceVersion.item_id == item.id)
        .order_by(MarketplaceVersion.version_number.desc())
    )
    versions = (await db.execute(stmt)).scalars().all()
    return [MarketplaceVersionSummary.model_validate(v) for v in versions]


async def get_version(
    db: AsyncSession,
    *,
    user: CurrentUser,
    context: OrgContext,
    version_id: uuid.UUID,
    authorize: ItemAuthorizer | None = None,
) -> MarketplaceVersionDetail | None:
    await require_private_actor(db, context)
    parent_id = await db.scalar(
        select(MarketplaceVersion.item_id)
        .where(MarketplaceVersion.id == version_id)
        .execution_options(autoflush=False)
    )
    if parent_id is None:
        return None
    with db.no_autoflush:
        item = await get_item(
            db, user=user, item_id=parent_id, context=context, authorize=authorize
        )
    if item is None:
        return None
    version = await load_item_version(db, item.id, version_id)
    if version is None:
        return None
    # Install permission required to reveal full payload? Spec §10.2 says
    # any viewer may read version metadata, so use can_view_item gate
    # only — install_service handles material download.
    return MarketplaceVersionDetail.model_validate(version)


# Re-exports for router import convenience.
__all__ = [
    "get_item",
    "get_version",
    "list_items",
    "list_items_page",
    "list_versions",
    "project_item",
]


# Silence unused import — ``can_install_item`` is referenced by future
# slices; keep the import here so the module's surface is stable.
_ = can_install_item
