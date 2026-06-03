"""Marketplace API — catalog (Slice A).

Read-only endpoints. Write side (install/publish/admin) is intentionally
absent in Slice A; later slices add their routers here.

Surface (Spec §10.1~§10.2):

* ``GET /api/marketplace/items``                — list catalog (filtered)
* ``GET /api/marketplace/items/{item_id}``      — detail
* ``GET /api/marketplace/items/{item_id}/versions``
* ``GET /api/marketplace/versions/{version_id}``

All endpoints require auth (``Depends(get_current_user)``). Visibility
gating happens in ``marketplace.service`` — when an item exists but is
not visible to the caller we return the same ``MARKETPLACE_ITEM_NOT_FOUND``
as the missing case (rules/security.md — enumeration oracle).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    require_super_user,
    verify_csrf,
)
from app.error_codes import (
    marketplace_item_not_found,
    marketplace_version_not_found,
)
from app.marketplace import install_service, publish_service
from app.marketplace import service as catalog_service
from app.marketplace.schemas import (
    InstallMarketplaceItemIn,
    MarketplaceInstallationOut,
    MarketplaceItemACLIn,
    MarketplaceItemAdminListedIn,
    MarketplaceItemListFilters,
    MarketplaceItemOut,
    MarketplaceItemPatchIn,
    MarketplaceItemsPage,
    MarketplaceVersionDetail,
    MarketplaceVersionFromSkillIn,
    MarketplaceVersionSummary,
    PublishSkillIn,
    UpdateMarketplaceInstallationIn,
)
from app.models.marketplace import MarketplaceItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/items", response_model=list[MarketplaceItemOut])
async def list_items(
    resource_type: str | None = Query(default=None, pattern="^(agent|mcp|skill)$"),
    q: str | None = Query(default=None, max_length=120),
    visibility: list[str] | None = Query(default=None),
    category: list[str] | None = Query(default=None),
    installed: bool | None = Query(default=None),
    install_state: str | None = Query(
        default=None, pattern="^(active|needs_setup|disabled|uninstalled)$"
    ),
    support_level: str | None = Query(default=None, max_length=40),
    source_kind: str | None = Query(default=None, max_length=40),
    is_listed: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[MarketplaceItemOut]:
    filters = MarketplaceItemListFilters(
        resource_type=resource_type,  # type: ignore[arg-type]
        q=q,
        visibility=visibility,  # type: ignore[arg-type]
        category=category,
        installed=installed,
        install_state=install_state,  # type: ignore[arg-type]
        support_level=support_level,
        source_kind=source_kind,
        is_listed=is_listed,
    )
    return list(
        await catalog_service.list_items(
            db, user=user, filters=filters, limit=limit, offset=offset
        )
    )


@router.get("/items/page", response_model=MarketplaceItemsPage)
async def list_items_page(
    resource_type: str | None = Query(default=None, pattern="^(agent|mcp|skill)$"),
    q: str | None = Query(default=None, max_length=120),
    visibility: list[str] | None = Query(default=None),
    category: list[str] | None = Query(default=None),
    installed: bool | None = Query(default=None),
    install_state: str | None = Query(
        default=None, pattern="^(active|needs_setup|disabled|uninstalled)$"
    ),
    support_level: str | None = Query(default=None, max_length=40),
    source_kind: str | None = Query(default=None, max_length=40),
    is_listed: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> MarketplaceItemsPage:
    filters = MarketplaceItemListFilters(
        resource_type=resource_type,  # type: ignore[arg-type]
        q=q,
        visibility=visibility,  # type: ignore[arg-type]
        category=category,
        installed=installed,
        install_state=install_state,  # type: ignore[arg-type]
        support_level=support_level,
        source_kind=source_kind,
        is_listed=is_listed,
    )
    return await catalog_service.list_items_page(
        db, user=user, filters=filters, limit=limit, offset=offset
    )


@router.get("/items/{item_id}", response_model=MarketplaceItemOut)
async def get_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> MarketplaceItemOut:
    item = await catalog_service.get_item(db, user=user, item_id=item_id)
    if item is None:
        # Branch (not-exists vs forbidden) is logged for ops only.
        logger.info(
            "marketplace_item_not_found user=%s item=%s",
            user.id,
            item_id,
        )
        raise marketplace_item_not_found()
    return await catalog_service.project_item(db, item=item, user=user)


@router.get(
    "/items/{item_id}/versions", response_model=list[MarketplaceVersionSummary]
)
async def list_versions(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[MarketplaceVersionSummary]:
    versions = await catalog_service.list_versions(db, user=user, item_id=item_id)
    if versions is None:
        logger.info(
            "marketplace_item_not_found user=%s item=%s (versions list)",
            user.id,
            item_id,
        )
        raise marketplace_item_not_found()
    return list(versions)


@router.get("/versions/{version_id}", response_model=MarketplaceVersionDetail)
async def get_version(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> MarketplaceVersionDetail:
    version = await catalog_service.get_version(db, user=user, version_id=version_id)
    if version is None:
        logger.info(
            "marketplace_version_not_found user=%s version=%s",
            user.id,
            version_id,
        )
        raise marketplace_version_not_found()
    return version


# ---------------------------------------------------------------------------
# Install / update / uninstall (Slice B — Spec §10.3)
# ---------------------------------------------------------------------------


@router.post(
    "/items/{item_id}/install",
    response_model=MarketplaceInstallationOut,
    status_code=201,
)
async def install_item(
    item_id: uuid.UUID,
    body: InstallMarketplaceItemIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceInstallationOut:
    """Install a marketplace item into the user's account.

    Returns ``201`` for a fresh install AND for a ``reuse_or_update``
    that reused an existing installation — the client uses the
    ``installation_id`` regardless of whether it was just created.
    """

    installation = await install_service.install_item(
        db, item_id=item_id, user=user, body=body
    )
    await db.commit()
    await db.refresh(installation)
    return MarketplaceInstallationOut.model_validate(installation)


@router.post(
    "/installations/{installation_id}/update",
    response_model=MarketplaceInstallationOut,
)
async def update_installation(
    installation_id: uuid.UUID,
    body: UpdateMarketplaceInstallationIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceInstallationOut:
    installation = await install_service.update_installation(
        db, installation_id=installation_id, user=user, body=body
    )
    await db.commit()
    await db.refresh(installation)
    return MarketplaceInstallationOut.model_validate(installation)


@router.delete(
    "/installations/{installation_id}",
    status_code=204,
)
async def delete_installation(
    installation_id: uuid.UUID,
    delete_resource: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    await install_service.delete_installation(
        db,
        installation_id=installation_id,
        user=user,
        delete_resource=delete_resource,
    )
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Publish / manage (Slice C — Spec §10.4)
# ---------------------------------------------------------------------------


@router.post(
    "/items/from-skill/{skill_id}",
    response_model=MarketplaceItemOut,
    status_code=201,
)
async def publish_item_from_skill(
    skill_id: uuid.UUID,
    body: PublishSkillIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    """First publish — creates a new marketplace item.

    Use ``POST /items/{id}/versions/from-skill/{skill_id}`` to add a
    follow-up version on an existing item; this endpoint always creates
    a fresh item (``body.item_id`` is ignored on this route).
    """

    body_copy = body.model_copy(update={"item_id": None})
    item = await publish_service.publish_skill(
        db, skill_id=skill_id, user=user, body=body_copy
    )
    await db.commit()
    # Re-fetch via the catalog service so the projection has
    # ``latest_version`` + ``acl_entries`` eager-loaded (avoids
    # MissingGreenlet on lazy access in async context).
    loaded = await catalog_service.get_item(db, user=user, item_id=item.id)
    if loaded is None:
        raise marketplace_item_not_found()
    return await catalog_service.project_item(db, item=loaded, user=user)


@router.post(
    "/items/{item_id}/versions/from-skill/{skill_id}",
    response_model=MarketplaceItemOut,
)
async def publish_new_version(
    item_id: uuid.UUID,
    skill_id: uuid.UUID,
    body: MarketplaceVersionFromSkillIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    """Add a new version to an existing item.

    The publish service requires the full ``PublishSkillIn`` shape so we
    synthesize one from the existing item's current metadata + the
    caller's ``release_notes``. Visibility / ACL stay at the item's
    current settings — use ``PATCH`` for metadata edits.
    """

    # Fetch via the catalog service so ``acl_entries`` is eager-loaded
    # (lazy access would trip MissingGreenlet under the async session).
    item = await catalog_service.get_item(db, user=user, item_id=item_id)
    if item is None:
        raise marketplace_item_not_found()

    publish_body = PublishSkillIn(
        item_id=item.id,
        visibility=item.visibility,  # type: ignore[arg-type]
        name=item.name,
        description=item.description,
        tags=list(item.tags or []),
        categories=list(item.categories or []),
        release_notes=body.release_notes,
        credential_requirements=[],
        acl_user_ids=[a.user_id for a in (item.acl_entries or [])],
    )

    updated = await publish_service.publish_skill(
        db, skill_id=skill_id, user=user, body=publish_body
    )
    await db.commit()
    await db.refresh(updated)
    return await catalog_service.project_item(db, item=updated, user=user)


@router.patch("/items/{item_id}", response_model=MarketplaceItemOut)
async def patch_item(
    item_id: uuid.UUID,
    body: MarketplaceItemPatchIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    item = await publish_service.patch_item(
        db, item_id=item_id, user=user, body=body
    )
    await db.commit()
    # Re-fetch via the catalog service so the projection has
    # ``latest_version`` + ``acl_entries`` eager-loaded (avoids
    # MissingGreenlet on lazy access in async context).
    loaded = await catalog_service.get_item(db, user=user, item_id=item.id)
    if loaded is None:
        raise marketplace_item_not_found()
    return await catalog_service.project_item(db, item=loaded, user=user)


@router.post(
    "/items/{item_id}/acl", response_model=MarketplaceItemOut
)
async def replace_item_acl(
    item_id: uuid.UUID,
    body: MarketplaceItemACLIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    item = await publish_service.replace_acl(
        db, item_id=item_id, user=user, body=body
    )
    await db.commit()
    # Re-fetch via the catalog service so the projection has
    # ``latest_version`` + ``acl_entries`` eager-loaded (avoids
    # MissingGreenlet on lazy access in async context).
    loaded = await catalog_service.get_item(db, user=user, item_id=item.id)
    if loaded is None:
        raise marketplace_item_not_found()
    return await catalog_service.project_item(db, item=loaded, user=user)


@router.delete(
    "/items/{item_id}/acl/{user_id_to_remove}", status_code=204
)
async def delete_item_acl_entry(
    item_id: uuid.UUID,
    user_id_to_remove: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    await publish_service.remove_acl_entry(
        db,
        item_id=item_id,
        user_id_to_remove=user_id_to_remove,
        user=user,
    )
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/items/{item_id}/disable", response_model=MarketplaceItemOut
)
async def disable_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    item = await publish_service.disable_item(
        db, item_id=item_id, user=user
    )
    await db.commit()
    # Re-fetch via the catalog service so the projection has
    # ``latest_version`` + ``acl_entries`` eager-loaded (avoids
    # MissingGreenlet on lazy access in async context).
    loaded = await catalog_service.get_item(db, user=user, item_id=item.id)
    if loaded is None:
        raise marketplace_item_not_found()
    return await catalog_service.project_item(db, item=loaded, user=user)


@router.post(
    "/items/{item_id}/enable", response_model=MarketplaceItemOut
)
async def enable_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    """Disable 의 inverse — ``status: disabled → published`` 로 복원. ACL
    / visibility / is_listed 는 그대로 유지된다 — owner 가 다시 노출하려면
    별도 흐름(visibility 변경 + super_user listing approve) 을 거친다.
    """

    item = await publish_service.enable_item(db, item_id=item_id, user=user)
    await db.commit()
    loaded = await catalog_service.get_item(db, user=user, item_id=item.id)
    if loaded is None:
        raise marketplace_item_not_found()
    return await catalog_service.project_item(db, item=loaded, user=user)


# ---------------------------------------------------------------------------
# Admin (super_user) — listing approval (Spec §10.5)
# ---------------------------------------------------------------------------


@router.post(
    "/admin/items/{item_id}/listed",
    response_model=MarketplaceItemOut,
)
async def admin_set_item_listed(
    item_id: uuid.UUID,
    body: MarketplaceItemAdminListedIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    """Spec §10.5 — super_user가 public item의 ``is_listed``를 토글한다.

    카탈로그 default filter는 ``is_listed=True``인 public 항목만 검색
    결과에 노출한다 (PRD §11.7). 부적절한 public 항목을 unlist하거나
    pending moderation에서 approve할 때 사용한다. CSRF 검증 필수.
    """

    item = await db.get(MarketplaceItem, item_id)
    if item is None:
        # 404 enumeration oracle (Spec §10.7).
        raise marketplace_item_not_found()

    item.is_listed = body.is_listed
    await db.flush()
    await db.refresh(item)

    loaded = await catalog_service.get_item(db, item_id=item_id, user=user)
    if loaded is None:
        raise marketplace_item_not_found()
    return await catalog_service.project_item(db, item=loaded, user=user)


# ---------------------------------------------------------------------------
# Admin (super_user) — k-skill sync status (Spec §10.4 admin)
# ---------------------------------------------------------------------------


@router.post("/admin/k-skill/sync")
async def admin_k_skill_sync_status(
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    """Spec §10.4 — operator inspection endpoint.

    **Does not execute** the sync — that's an out-of-band CLI run
    (``uv run python -m app.scripts.sync_k_skill``). This endpoint just
    surfaces the current population: how many k-skill items exist,
    their statuses, and the most recent ``updated_at`` so the dashboard
    can tell whether a fresh CLI run is overdue.
    """

    rows = (
        await db.execute(
            select(MarketplaceItem)
            .where(MarketplaceItem.source_kind == "k-skill")
            .order_by(MarketplaceItem.updated_at.desc())
        )
    ).scalars().all()

    return {
        "count": len(rows),
        "last_updated_at": rows[0].updated_at.isoformat() if rows else None,
        "items": [
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "status": r.status,
                "source_external_id": r.source_external_id,
                "latest_version_id": str(r.latest_version_id) if r.latest_version_id else None,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ],
    }
