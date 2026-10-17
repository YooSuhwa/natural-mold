"""Shared mutation context, ``can_manage_tenant`` checks and company audit for S30."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.config import settings
from app.dependencies import CurrentUser
from app.models.organization import Tenant
from app.services import audit_service
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)


@dataclass(frozen=True, slots=True)
class SsoMutation:
    db: AsyncSession
    request: Request
    actor: CurrentUser


def require_manage(context: TenantAdminContext) -> None:
    """S30 is company-admin only, reads included (domains and IdP settings)."""
    if "can_manage_tenant" not in context.permissions:
        raise authorization_error("permission_required")


async def locked_admin(mutation: SsoMutation, context: TenantAdminContext) -> Tenant:
    """Re-resolve authority under the Tenant row lock before any write."""
    fresh = await load_tenant_admin_context(
        mutation.db,
        context.actor,
        TenantAdminTarget(context.tenant.id, permission="can_manage_tenant"),
    )
    return fresh.tenant


async def audit(
    mutation: SsoMutation,
    tenant: Tenant,
    action: str,
    target: tuple[str, UUID, str | None],
    metadata: dict[str, JsonValue] | None = None,
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
        tenant_id=tenant.id,
        metadata={"tenant_id": str(tenant.id), **(metadata or {})},
    )


def public_url(request: Request, route: str, connection_id: UUID) -> str:
    """``SSO_PUBLIC_BASE_URL`` + the route path when configured, else the request URL."""
    path = request.app.url_path_for(route, connection_id=str(connection_id))
    base = settings.sso_public_base_url.strip().rstrip("/")
    if base:
        return base + str(path)
    return str(request.url_for(route, connection_id=str(connection_id)))


def callback_url(request: Request, connection_id: UUID) -> str:
    return public_url(request, "sso_callback", connection_id)
