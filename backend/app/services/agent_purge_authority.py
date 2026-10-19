"""Fresh authorization for replaying a purge after its Agent row is gone."""

from __future__ import annotations

from sqlalchemy import select

from app.authz.error_codes import (
    agent_lifecycle_permission_required,
    agent_purge_name_mismatch,
)
from app.authz.org_administrators import active_administrators
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent_purge import AgentPurgeReceipt
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.services.agent_purge_types import AgentPurgeCommand
from app.tenancy.context import resolve_tenant_context


async def authorize_purge_replay(command: AgentPurgeCommand) -> AgentPurgeReceipt:
    """Return a locked receipt only to a fresh effective active org-admin."""
    db = command.db
    receipt = await db.scalar(
        select(AgentPurgeReceipt)
        .where(AgentPurgeReceipt.agent_id_snapshot == command.agent_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if receipt is None:
        raise agent_not_found()
    actor_id = await db.scalar(
        select(User.id)
        .where(User.id == command.actor.id, User.is_active.is_(True))
        .execution_options(populate_existing=True)
    )
    if actor_id is None:
        raise agent_not_found()
    try:
        company = await resolve_tenant_context(db, command.actor, receipt.tenant_id)
    except AppError as error:
        raise agent_not_found() from error
    organization = await db.scalar(
        select(Organization).where(
            Organization.id == receipt.org_id,
            Organization.tenant_id == receipt.tenant_id,
            Organization.status == "active",
        )
    )
    if organization is None:
        raise agent_not_found()
    member = await db.scalar(
        select(OrganizationMember.user_id).where(
            OrganizationMember.org_id == receipt.org_id,
            OrganizationMember.tenant_id == receipt.tenant_id,
            OrganizationMember.user_id == actor_id,
            OrganizationMember.status == "active",
        )
    )
    company_admin = "admin" in company.roles
    if member is None and not company_admin:
        raise agent_not_found()
    org_admin = member is not None and actor_id in await active_administrators(db, organization)
    if not org_admin:
        raise agent_lifecycle_permission_required()
    if command.confirmation_name != receipt.agent_name_snapshot:
        raise agent_purge_name_mismatch()
    return receipt


__all__ = ["authorize_purge_replay"]
