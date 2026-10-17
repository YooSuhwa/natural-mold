"""Metadata-only authority for Agent archive, restore, and purge governance."""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, assert_never
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import (
    agent_lifecycle_permission_required,
    agent_purge_name_mismatch,
)
from app.authz.org_administrators import active_administrators
from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import CurrentUser
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.schemas.retention import trash_delete_at
from app.services import audit_service
from app.tenancy.context import resolve_tenant_context

AgentLifecycleAction = Literal["archive", "restore", "purge"]
AgentLifecycleState = Literal["active", "archived_stopping", "archived_stopped", "purged"]
# Set by the E-08 retention sweep around ``purge_agent``: extra ``agent.purge`` audit
# metadata marking a system purge (audit only; it grants nothing).
PURGE_AUDIT_METADATA: ContextVar[Mapping[str, object] | None] = ContextVar(
    "agent_purge_audit_metadata", default=None
)


@dataclass(frozen=True, slots=True)
class AgentLifecycleAuthorization:
    agent_id: UUID
    action: AgentLifecycleAction
    confirmation_name: str | None = None
    context: OrgContext | None = None


@dataclass(frozen=True, slots=True)
class AgentLifecycleAuthority:
    agent_id: UUID
    name: str
    owner_user_id: UUID
    owner_label: str
    archived_by_user_id: UUID | None
    archived_at: datetime | None
    lifecycle_state: AgentLifecycleState
    policy_delete_at: datetime | None
    can_archive: bool
    can_restore: bool
    can_purge: bool
    actor_user_id: UUID
    actor_email: str
    actor_label: str


async def load_agent_lifecycle_authority(
    db: AsyncSession, actor: CurrentUser, agent_id: UUID
) -> AgentLifecycleAuthority:
    """Reload target metadata and current governance geometry without private hydration."""
    with db.no_autoflush:
        target = (
            await db.execute(
                select(
                    Agent.id,
                    Agent.name,
                    Agent.user_id,
                    Agent.org_id,
                    Agent.tenant_id,
                    Agent.archived_by,
                    Agent.archived_at,
                    User.display_name,
                    User.name.label("owner_name"),
                )
                .join(User, User.id == Agent.user_id)
                .where(
                    Agent.id == agent_id,
                    Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                )
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if target is None:
            raise agent_not_found()
        persisted_actor = (
            await db.execute(
                select(User.id, User.email, User.name, User.is_active)
                .where(User.id == actor.id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if persisted_actor is None or not persisted_actor.is_active:
            raise agent_not_found()
        try:
            company = await resolve_tenant_context(db, actor, target.tenant_id)
        except AppError as error:
            raise agent_not_found() from error
        organization = await db.scalar(
            select(Organization)
            .where(
                Organization.id == target.org_id,
                Organization.tenant_id == target.tenant_id,
                Organization.status == "active",
            )
            .execution_options(populate_existing=True)
        )
        if organization is None:
            raise agent_not_found()
        active_member = await db.scalar(
            select(OrganizationMember.user_id).where(
                OrganizationMember.org_id == target.org_id,
                OrganizationMember.tenant_id == target.tenant_id,
                OrganizationMember.user_id == actor.id,
                OrganizationMember.status == "active",
            )
        )
        company_admin = "admin" in company.roles
        if active_member is None and not company_admin:
            raise agent_not_found()
        org_admin = active_member is not None and actor.id in await active_administrators(
            db, organization
        )
        owner = active_member is not None and actor.id == target.user_id
        can_archive = owner or org_admin or company_admin
        can_restore = owner or org_admin
        can_purge = org_admin
        if not (can_archive or can_restore or can_purge):
            raise agent_lifecycle_permission_required()
        return AgentLifecycleAuthority(
            agent_id=target.id,
            name=target.name,
            owner_user_id=target.user_id,
            owner_label=target.display_name or target.owner_name,
            archived_by_user_id=target.archived_by,
            archived_at=target.archived_at,
            lifecycle_state="active" if target.archived_at is None else "archived_stopping",
            policy_delete_at=trash_delete_at(target.archived_at, organization.settings)
            if settings.retention_sweep_enabled and target.archived_at is not None
            else None,
            can_archive=can_archive,
            can_restore=can_restore,
            can_purge=can_purge,
            actor_user_id=persisted_actor.id,
            actor_email=persisted_actor.email,
            actor_label=persisted_actor.name,
        )


async def authorize_agent_lifecycle(
    db: AsyncSession, actor: CurrentUser, request: AgentLifecycleAuthorization
) -> AgentLifecycleAuthority:
    """Authorize one lifecycle action against fresh persisted authority."""
    if request.context is not None:
        if request.context.user_id != actor.id:
            raise agent_not_found()
        scoped_agent_id = await db.scalar(
            select(Agent.id).where(
                Agent.id == request.agent_id,
                Agent.org_id == request.context.org_id,
                Agent.tenant_id == request.context.tenant_id,
                Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            )
        )
        if scoped_agent_id is None:
            raise agent_not_found()
    authority = await load_agent_lifecycle_authority(db, actor, request.agent_id)
    match request.action:
        case "archive":
            allowed = authority.can_archive
        case "restore":
            allowed = authority.can_restore
        case "purge":
            allowed = authority.can_purge
        case unreachable:
            assert_never(unreachable)
    if not allowed:
        raise agent_lifecycle_permission_required()
    if request.action == "purge" and request.confirmation_name != authority.name:
        raise agent_purge_name_mismatch()
    return authority


async def record_agent_lifecycle_success(
    db: AsyncSession, authority: AgentLifecycleAuthority, action: AgentLifecycleAction
) -> None:
    """Persist successful governance with the actual actor and target owner identities."""
    match action:
        case "archive":
            audit_action = audit_actions.AGENT_ARCHIVE
        case "restore":
            audit_action = audit_actions.AGENT_RESTORE
        case "purge":
            audit_action = audit_actions.AGENT_PURGE
        case unreachable:
            assert_never(unreachable)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=authority.actor_user_id,
        actor_email_snapshot=authority.actor_email,
        actor_label=authority.actor_label,
        owner_user_id=authority.owner_user_id,
        action=audit_action,
        target_type="agent",
        target_id=authority.agent_id,
        target_name_snapshot=authority.name,
        target_owner_user_id=authority.owner_user_id,
        outcome="success",
        metadata=dict(extra)
        if action == "purge" and (extra := PURGE_AUDIT_METADATA.get())
        else None,
    )
