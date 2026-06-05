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

from fastapi import APIRouter, Depends, Query, Request, Response
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
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem
from app.services import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


def _item_metadata(item: MarketplaceItem) -> dict[str, object]:
    return {
        "resource_type": item.resource_type,
        "visibility": item.visibility,
        "status": item.status,
        "is_listed": item.is_listed,
        "is_system": item.is_system,
        "source_kind": item.source_kind,
        "latest_version_id": str(item.latest_version_id) if item.latest_version_id else None,
        "tag_count": len(item.tags or []),
        "category_count": len(item.categories or []),
    }


async def _record_marketplace_item_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    item: MarketplaceItem,
    metadata: dict[str, object] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=item.owner_user_id,
        action=action,
        target_type="marketplace_item",
        target_id=item.id,
        target_name_snapshot=item.name,
        target_owner_user_id=item.owner_user_id,
        outcome="success",
        request=request,
        metadata={**_item_metadata(item), **(metadata or {})},
    )


async def _record_marketplace_installation_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    installation: MarketplaceInstallation,
    metadata: dict[str, object] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="marketplace_installation",
        target_id=installation.id,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "item_id": str(installation.item_id),
            "version_id": str(installation.version_id),
            "resource_type": installation.resource_type,
            "install_status": installation.install_status,
            "installed_skill_id": (
                str(installation.installed_skill_id)
                if installation.installed_skill_id
                else None
            ),
            "is_dirty": installation.is_dirty,
            **(metadata or {}),
        },
    )


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
    request: Request,
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
    await _record_marketplace_installation_audit(
        db,
        user=user,
        request=request,
        action="marketplace.install",
        installation=installation,
        metadata={
            "install_mode": body.install_mode,
            "credential_binding_count": len(body.credential_bindings or {}),
        },
    )
    await db.commit()
    return MarketplaceInstallationOut.model_validate(installation)


@router.post(
    "/installations/{installation_id}/update",
    response_model=MarketplaceInstallationOut,
)
async def update_installation(
    installation_id: uuid.UUID,
    body: UpdateMarketplaceInstallationIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceInstallationOut:
    installation = await install_service.update_installation(
        db, installation_id=installation_id, user=user, body=body
    )
    await db.commit()
    await db.refresh(installation)
    await _record_marketplace_installation_audit(
        db,
        user=user,
        request=request,
        action="marketplace.installation_update",
        installation=installation,
        metadata={"strategy": body.strategy},
    )
    await db.commit()
    return MarketplaceInstallationOut.model_validate(installation)


@router.delete(
    "/installations/{installation_id}",
    status_code=204,
)
async def delete_installation(
    installation_id: uuid.UUID,
    request: Request,
    delete_resource: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    installation = (
        await db.execute(
            select(MarketplaceInstallation).where(
                MarketplaceInstallation.id == installation_id,
                MarketplaceInstallation.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    await install_service.delete_installation(
        db,
        installation_id=installation_id,
        user=user,
        delete_resource=delete_resource,
    )
    if installation is not None:
        await _record_marketplace_installation_audit(
            db,
            user=user,
            request=request,
            action="marketplace.installation_delete",
            installation=installation,
            metadata={"delete_resource": delete_resource},
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
    request: Request,
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
    await db.refresh(item)
    await _record_marketplace_item_audit(
        db,
        user=user,
        request=request,
        action="marketplace.publish",
        item=item,
        metadata={
            "skill_id": str(skill_id),
            "release_notes_present": bool(body.release_notes),
            "credential_requirement_count": len(body.credential_requirements or []),
            "acl_user_count": len(body.acl_user_ids or []),
        },
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
    request: Request,
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
    await _record_marketplace_item_audit(
        db,
        user=user,
        request=request,
        action="marketplace.version_publish",
        item=updated,
        metadata={
            "skill_id": str(skill_id),
            "release_notes_present": bool(body.release_notes),
        },
    )
    await db.commit()
    return await catalog_service.project_item(db, item=updated, user=user)


@router.patch("/items/{item_id}", response_model=MarketplaceItemOut)
async def patch_item(
    item_id: uuid.UUID,
    body: MarketplaceItemPatchIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    item = await publish_service.patch_item(
        db, item_id=item_id, user=user, body=body
    )
    await db.commit()
    await db.refresh(item)
    await _record_marketplace_item_audit(
        db,
        user=user,
        request=request,
        action="marketplace.item_update",
        item=item,
        metadata={"changed_fields": sorted(body.model_fields_set)},
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    item = await publish_service.replace_acl(
        db, item_id=item_id, user=user, body=body
    )
    await db.commit()
    await db.refresh(item)
    await _record_marketplace_item_audit(
        db,
        user=user,
        request=request,
        action="marketplace.item_acl_replace",
        item=item,
        metadata={"acl_user_count": len(body.user_ids), "permission": body.permission},
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    item = await db.get(MarketplaceItem, item_id)
    await publish_service.remove_acl_entry(
        db,
        item_id=item_id,
        user_id_to_remove=user_id_to_remove,
        user=user,
    )
    if item is not None:
        await _record_marketplace_item_audit(
            db,
            user=user,
            request=request,
            action="marketplace.item_acl_delete",
            item=item,
            metadata={"user_id_removed": str(user_id_to_remove)},
        )
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/items/{item_id}/disable", response_model=MarketplaceItemOut
)
async def disable_item(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MarketplaceItemOut:
    item = await publish_service.disable_item(
        db, item_id=item_id, user=user
    )
    await db.commit()
    await db.refresh(item)
    await _record_marketplace_item_audit(
        db,
        user=user,
        request=request,
        action="marketplace.item_disable",
        item=item,
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
    request: Request,
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
    await db.refresh(item)
    await _record_marketplace_item_audit(
        db,
        user=user,
        request=request,
        action="marketplace.item_enable",
        item=item,
    )
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
    request: Request,
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
    await _record_marketplace_item_audit(
        db,
        user=user,
        request=request,
        action="marketplace.admin_set_listed",
        item=item,
        metadata={"is_listed": body.is_listed},
    )
    await db.commit()

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
