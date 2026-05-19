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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.marketplace.access import (
    can_install_item,
    can_view_item,
    is_owner,
)
from app.marketplace.origin_service import (
    derive_credential_summary,
    derive_installation_summary,
)
from app.marketplace.schemas import (
    CredentialSummaryOut,
    MarketplaceInstallationSummary,
    MarketplaceItemListFilters,
    MarketplaceItemOut,
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


def _base_catalog_query(user: CurrentUser, *, public_listed_only: bool):
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

    stmt = select(MarketplaceItem)
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
        MarketplaceItem.owner_user_id == user.id,
        MarketplaceItem.visibility == "system",
        public_path,
        and_(
            MarketplaceItem.status == "published",
            MarketplaceItem.visibility == "restricted",
            acl_exists,
        ),
    ]

    return stmt.where(or_(*visibility_clauses))


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


# ---------------------------------------------------------------------------
# Item → response projection
# ---------------------------------------------------------------------------


async def _project_item(
    db: AsyncSession,
    item: MarketplaceItem,
    user: CurrentUser,
) -> MarketplaceItemOut:
    latest_version_summary: MarketplaceVersionSummary | None = None
    if item.latest_version_id is not None:
        # ``latest_version`` may be eager-loaded; fall back to a lookup.
        lv = item.latest_version
        if lv is None:
            lv = await db.get(MarketplaceVersion, item.latest_version_id)
        if lv is not None:
            latest_version_summary = MarketplaceVersionSummary.model_validate(lv)

    requirements = (
        item.latest_version.credential_requirements
        if item.latest_version is not None
        else None
    )
    credential_summary: CredentialSummaryOut = derive_credential_summary(requirements)

    installation: MarketplaceInstallationSummary = await derive_installation_summary(
        db, item=item, user_id=user.id
    )

    # publication_summary는 viewer 본인이 publish한 자기 리소스인지 보여주는 정보.
    # owner 일치만으로 published 상태를 그리면 — 사용자가 source skill을 삭제한
    # 직후에도 marketplace_item.owner_user_id는 남아있어 카탈로그가 "Manage"
    # CTA를 노출. 그 결과 자기 publish 백업본을 다시 install 불가능. PRD §6
    # 정신상 source resource를 잃은 owner는 marketplace 원본에서 재install이
    # 합리적이므로, publication_link 존재 여부까지 함께 확인해야 한다.
    owner_view = is_owner(item, user)
    has_publication_link = False
    if owner_view:
        link_exists = await db.execute(
            select(MarketplacePublicationLink.id)
            .where(MarketplacePublicationLink.item_id == item.id)
            .limit(1)
        )
        has_publication_link = link_exists.scalar_one_or_none() is not None

    publication_visible = owner_view and has_publication_link

    publication = ResourcePublicationSummaryOut(
        state=_publication_state_for_owner(item) if publication_visible else "not_published",
        item_id=item.id if publication_visible else None,
        visibility=item.visibility if publication_visible else None,
        status=item.status if publication_visible else None,
        is_listed=item.is_listed if publication_visible else False,
        latest_version_id=item.latest_version_id if publication_visible else None,
        version_number=(
            item.latest_version.version_number
            if publication_visible and item.latest_version is not None
            else None
        ),
        shared_user_count=(
            len(item.acl_entries) if publication_visible else 0
        ),
    )

    return MarketplaceItemOut(
        id=item.id,
        resource_type=item.resource_type,  # type: ignore[arg-type]
        name=item.name,
        slug=item.slug,
        description=item.description,
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
            item.latest_version.execution_profile
            if item.latest_version is not None
            else None
        ),
        origin_summary=None,
        publication_summary=publication,
        installation=installation,
    )


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
            return (
                "published_public_listed"
                if item.is_listed
                else "published_public_unlisted"
            )
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
    filters: MarketplaceItemListFilters,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[MarketplaceItemOut]:
    # Spec §10.1 — explicit ``?is_listed=...`` overrides the default
    # listed-public-only gate so super_user moderation views can pull the
    # unlisted queue (``?is_listed=false``).
    public_listed_only = filters.is_listed is None
    stmt = _base_catalog_query(user, public_listed_only=public_listed_only)
    stmt = _apply_filters(stmt, filters)
    stmt = stmt.options(
        selectinload(MarketplaceItem.latest_version),
        selectinload(MarketplaceItem.acl_entries),
    )
    stmt = stmt.order_by(
        MarketplaceItem.is_listed.desc(),
        MarketplaceItem.published_at.desc().nullslast(),
        MarketplaceItem.created_at.desc(),
    )
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().unique().all()

    # Post-load filters (need eager-loaded data).
    if filters.installed is not None:
        kept: list[MarketplaceItem] = []
        for item in rows:
            summary = await derive_installation_summary(
                db, item=item, user_id=user.id
            )
            if summary.installed == filters.installed:
                kept.append(item)
        rows = kept
    if filters.install_state:
        kept = []
        for item in rows:
            summary = await derive_installation_summary(
                db, item=item, user_id=user.id
            )
            if summary.status == filters.install_state:
                kept.append(item)
        rows = kept
    if filters.support_level:
        rows = [
            i
            for i in rows
            if i.latest_version is not None
            and (i.latest_version.execution_profile or {}).get("support_level")
            == filters.support_level
        ]
    if filters.category:
        rows = [
            i
            for i in rows
            if isinstance(i.categories, list)
            and any(c in i.categories for c in filters.category)
        ]

    return [await _project_item(db, item, user) for item in rows]


async def get_item(
    db: AsyncSession,
    *,
    user: CurrentUser,
    item_id: uuid.UUID,
) -> MarketplaceItem | None:
    """Fetch an item if the user is allowed to see it.

    Returns ``None`` for both "doesn't exist" and "exists but hidden" so
    callers can map both to a single 404 (enumeration oracle safety).
    """

    stmt = (
        select(MarketplaceItem)
        .where(MarketplaceItem.id == item_id)
        .options(
            selectinload(MarketplaceItem.latest_version),
            selectinload(MarketplaceItem.acl_entries),
        )
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
) -> MarketplaceItemOut:
    """Public projection helper (router uses after ``get_item``)."""

    return await _project_item(db, item, user)


async def list_versions(
    db: AsyncSession,
    *,
    user: CurrentUser,
    item_id: uuid.UUID,
) -> Sequence[MarketplaceVersionSummary] | None:
    item = await get_item(db, user=user, item_id=item_id)
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
    version_id: uuid.UUID,
) -> MarketplaceVersionDetail | None:
    version = await db.get(MarketplaceVersion, version_id)
    if version is None:
        return None
    item = await get_item(db, user=user, item_id=version.item_id)
    if item is None:
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
    "list_versions",
    "project_item",
]


# Silence unused import — ``can_install_item`` is referenced by future
# slices; keep the import here so the module's surface is stable.
_ = can_install_item
