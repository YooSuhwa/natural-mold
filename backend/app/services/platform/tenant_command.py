"""One operator command on a company, from the SaaS API or the operator CLI (I-07)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.dependencies import CurrentUser
from app.models.organization import Tenant
from app.services import audit_service
from app.services.platform.tenant_directory import PlatformTenantMutation

CLI_LABEL = "operator_cli"


@dataclass(frozen=True, slots=True)
class TenantCommand:
    db: AsyncSession
    tenant_id: UUID
    actor: CurrentUser | None  # None: the operator CLI (shell access, no account)
    expected_version: str | None = None
    request: Request | None = None

    @classmethod
    def from_mutation(cls, mutation: PlatformTenantMutation) -> "TenantCommand":
        return cls(
            mutation.db,
            mutation.tenant_id,
            mutation.actor,
            mutation.expected_version,
            mutation.request,
        )


async def lock_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    with db.no_autoflush:
        tenant = await db.scalar(
            select(Tenant)
            .where(Tenant.id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if tenant is None:
        raise authorization_error("not_found")
    return tenant


async def record(
    command: TenantCommand,
    tenant: Tenant,
    action: str,
    metadata: dict[str, Any],
    reason: str | None = None,
) -> None:
    """A platform ``tenant.*`` event (``tenant_id`` stays NULL, like suspend/resume)."""
    actor = command.actor
    await audit_service.record_event(
        command.db,
        actor_type="user" if actor is not None else "system",
        actor_user_id=None if actor is None else actor.id,
        actor_email_snapshot=None if actor is None else actor.email,
        actor_label=CLI_LABEL if actor is None else None,
        owner_user_id=None if actor is None else actor.id,
        action=action,
        target_type="tenant",
        target_id=tenant.id,
        target_name_snapshot=tenant.name,
        outcome="success",
        reason_message=reason,
        request=command.request,
        metadata={"tenant_id": str(tenant.id), **metadata},
    )
