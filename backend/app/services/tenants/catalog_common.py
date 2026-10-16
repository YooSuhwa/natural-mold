"""Shared pieces of the company settings tier routes (plan 9.7 ``can_manage_tenant_catalog``).

Lock order for writes matches the organization catalog PUT and the fanout backfill
(organization rows, then the company row, then objects): outside legacy mode a write first
locks every organization of the company in id order, then the company row through the
fresh ``can_manage_tenant_catalog`` check. The fanout re-sync, the commit and the outbox
apply run in ``commit_item_change`` (nothing is projected in legacy mode, 8.4).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_fanout import FanoutKind
from app.authz.catalog_item_fanout import sync_item_fanout
from app.authz.error_codes import authorization_error
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.service import AuthzService
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.organization import Organization, Tenant
from app.services import audit_service
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)

CATALOG_PERMISSION = "can_manage_tenant_catalog"


@dataclass(frozen=True, slots=True)
class TenantCatalogMutation:
    db: AsyncSession
    request: Request
    actor: CurrentUser
    authz: AuthzService


def require_catalog_manager(context: TenantAdminContext) -> None:
    """Reads need the same company permission as writes (9.7), without the write lock."""
    if CATALOG_PERMISSION not in context.permissions:
        raise AppError(
            code="permission_required", message="회사 관리 권한이 필요합니다", status=403
        )


async def lock_for_write(mutation: TenantCatalogMutation, tenant_id: UUID) -> TenantAdminContext:
    if mutation.authz.mode != "legacy":
        await mutation.db.scalars(
            select(Organization.id)
            .where(Organization.tenant_id == tenant_id)
            .order_by(Organization.id)
            .with_for_update()
        )
    return await load_tenant_admin_context(
        mutation.db, mutation.actor, TenantAdminTarget(tenant_id, permission=CATALOG_PERMISSION)
    )


async def require_company_orgs(
    db: AsyncSession, tenant_id: UUID, org_ids: Iterable[UUID]
) -> list[UUID]:
    """Designated organizations must be live organizations of this company (else 404)."""
    wanted = sorted(set(org_ids))
    if not wanted:
        return []
    found = set(
        await db.scalars(
            select(Organization.id).where(
                Organization.id.in_(wanted),
                Organization.tenant_id == tenant_id,
                Organization.status != "archived",
            )
        )
    )
    if found != set(wanted):
        raise authorization_error("not_found")
    return wanted


async def record(
    mutation: TenantCatalogMutation,
    tenant: Tenant,
    action: str,
    target: tuple[str, UUID, str],
    metadata: Mapping[str, object],
) -> None:
    target_type, target_id, name = target
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=mutation.actor.id,
        actor_email_snapshot=mutation.actor.email,
        owner_user_id=mutation.actor.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name_snapshot=name,
        outcome="success",
        request=mutation.request,
        metadata={"tenant_id": str(tenant.id), **metadata},
    )


async def commit_company_change(
    mutation: TenantCatalogMutation,
    tenant: Tenant,
    kind: FanoutKind | None,
    item_id: UUID | None,
    extra: LifecycleChange = LifecycleChange(),
) -> None:
    """Re-derive the change's fanout in this company's organizations (outside legacy),
    commit, then apply the queued rows (with ``extra`` lifecycle rows) before responding."""
    authz = mutation.authz
    outbox = extra.outbox_ids
    if authz.mode != "legacy":
        synced = await sync_item_fanout(
            mutation.db, kind, item_id, mutation.actor.id, tenant_id=tenant.id
        )
        outbox += synced.outbox_ids
    await mutation.db.commit()
    await apply_lifecycle(LifecycleChange(outbox), authz)
