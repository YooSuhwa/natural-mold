"""Shareable resources of the share/access/transfer API (plan 9.7, 10.1-10.3, E-01).

One table names, per type, the ORM row, the 8.4 minimum-visible relation (hidden → 404,
visible without the permission → 403) and the levels 10.1 allows. Only rows of the
current organization are targets: platform/company rows (``is_system``, org NULL,
company-scope credentials) are shared by catalog fanout, never by this API (D21).
Marketplace items keep their ACL routes as the grant writer until I-04 (10.5).

Legacy verdict: only the owner inside its organization may share, list, revoke or
transfer (the existing owner-only rule); OpenFGA decides once the type is enforced.
"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service import AuthzService
from app.authz.service_types import Verdict
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.skill import Skill
from app.models.tool import Tool

ShareRow = Agent | Skill | Tool | McpServer | Credential
FULL_LEVELS: Final = ("consumer", "viewer", "editor", "manager")


@dataclass(frozen=True, slots=True)
class ShareType:
    name: str
    model: type[ShareRow]
    min_visible: str
    levels: tuple[str, ...] = FULL_LEVELS


SHARE_TYPES: Final[dict[str, ShareType]] = {
    "agent": ShareType("agent", Agent, "can_run"),
    "skill": ShareType("skill", Skill, "can_use"),
    "tool": ShareType("tool", Tool, "can_use"),
    "mcp_server": ShareType("mcp_server", McpServer, "can_use"),
    "credential": ShareType("credential", Credential, "can_use", ("consumer", "manager")),
}


def share_object(kind: ShareType, resource_id: UUID) -> str:
    return f"{kind.name}:{resource_id}"


def is_owner(row: ShareRow, ctx: OrgContext) -> bool:
    return row.user_id == ctx.user_id and row.org_id == ctx.org_id


async def load_target(
    db: AsyncSession, ctx: OrgContext, kind: ShareType, resource_id: UUID
) -> ShareRow | None:
    """The current organization's own row, or ``None`` (missing, foreign, platform)."""
    row = await db.get(kind.model, resource_id, populate_existing=True)
    if row is None or row.org_id != ctx.org_id or row.tenant_id != ctx.tenant_id:
        return None
    if getattr(row, "is_system", False) or row.user_id is None:
        return None
    if isinstance(row, Credential) and row.scope != "personal":
        return None
    return row


async def authorize_target(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    kind: ShareType,
    resource_id: UUID,
    permission: str,
    *,
    min_visible: str | None = None,
) -> ShareRow:
    """8.4 decision for ``permission`` on the target; returns the loaded row."""
    row = await load_target(db, ctx, kind, resource_id)
    legacy = Verdict.ALLOW if row is not None and is_owner(row, ctx) else Verdict.NOT_FOUND
    await authz.require(
        Principal.for_user(ctx),
        permission,
        share_object(kind, resource_id),
        min_visible=min_visible or kind.min_visible,
        resource_org_id=None if row is None else row.org_id,
        legacy=legacy,
    )
    if row is None:  # unreachable: require refuses a missing row
        raise denials.not_found()
    return row


async def recheck_owner(
    db: AsyncSession, kind: ShareType, resource_id: UUID, owner: UUID | None
) -> None:
    """After ``GrantService``'s organization/grant locks, lock the resource row (single
    order org → grants → resource, as in transfer) and refuse when ownership moved since
    the decision was made."""
    model = kind.model
    current = await db.scalar(
        select(model.user_id).where(model.id == resource_id).with_for_update()
    )
    if current != owner:
        raise authorization_error("stale_version")


async def my_share_permissions(
    authz: AuthzService, ctx: OrgContext, kind: ShareType, row: ShareRow
) -> list[str]:
    perms = ["can_share", "can_revoke", "can_govern"]
    legacy = perms if is_owner(row, ctx) else []
    return await authz.my_permissions(
        Principal.for_user(ctx), share_object(kind, row.id), perms, legacy=legacy
    )
