"""10.2 sharing rules checked before ``GrantService`` writes (E-01).

* Principals: a user, group or the organization itself, all of the current organization
  (``grant_validation.check_principal`` enforces D21; agents are delegation-only).
* Levels: the type's 10.1 levels, at most ``manager``; ownership moves only by transfer,
  so the owner is never a grant principal.
* ``expires_at``: agent ``consumer`` (run) shares only (``non_expired_grant``).
* Organization-wide shares need ``cap_share_org_wide``.
* Credentials (personal accounts): only the owner shares them; a manager grantee or a
  company admin gets the 403 a visible caller without ``can_share`` gets.
* Self: a non-owner may lower their own direct grant, never raise or create one.
* D17: sharing an agent that runs on delegated account resources (credentials, or a
  tool/MCP server carrying its owner's credential) needs ``confirm_delegation``.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.error_codes import authorization_error
from app.authz.grant_validation import invalid
from app.authz.org_context import OrgContext
from app.authz.org_permissions import organization_permissions
from app.authz.service import AuthzService
from app.authz.share_targets import ShareRow, ShareType, is_owner
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.tool import Tool
from app.schemas.grant import GrantCreate

ORG_WIDE_CAP = "cap_share_org_wide"
_ACCOUNT_TYPES = {"credential": Credential, "tool": Tool, "mcp_server": McpServer}


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def check_level(kind: ShareType, row: ShareRow, body: GrantCreate) -> None:
    if body.level not in kind.levels:
        raise invalid("level")
    if body.expires_at is not None and (kind.name != "agent" or body.level != "consumer"):
        raise invalid("expires_at")
    if body.principal.type == "user" and body.principal.id == row.user_id:
        raise invalid("owner")  # the owner holds every level already; transfer moves it


_RANK: Final = {"consumer": 0, "viewer": 1, "editor": 2, "manager": 3}


def require_credential_owner(kind: ShareType, row: ShareRow, ctx: OrgContext) -> None:
    if kind.name == "credential" and not is_owner(row, ctx):
        raise denials.permission_required("can_share", request_access=False)


def check_self(ctx: OrgContext, body: GrantCreate, held: Sequence[ResourceGrant]) -> None:
    """``held``: the caller's own active direct grants on the resource."""
    if body.principal.type != "user" or body.principal.id != ctx.user_id:
        return
    if not any(_RANK.get(g.relation, -1) > _RANK[body.level] for g in held):
        raise invalid("self_grant")


async def require_org_wide(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, body: GrantCreate
) -> None:
    if body.principal.type != "organization":
        return
    _, capabilities = await organization_permissions(db, ctx.org_id, ctx.user_id)
    await authz.require_cap(ctx, ORG_WIDE_CAP, legacy=ORG_WIDE_CAP in capabilities)


async def delegated_accounts(db: AsyncSession, agent_id: UUID) -> list[dict[str, object]]:
    """Account-carrying resources delegated to the agent (10.6 grants, D17 list)."""
    grants = await db.scalars(
        select(ResourceGrant)
        .where(
            ResourceGrant.principal_type == "agent",
            ResourceGrant.principal_id == agent_id,
            ResourceGrant.grant_kind == "delegation",
            ResourceGrant.state == "active",
            ResourceGrant.resource_type.in_(tuple(_ACCOUNT_TYPES)),
        )
        .order_by(ResourceGrant.resource_type, ResourceGrant.resource_id)
    )
    found: list[dict[str, object]] = []
    for grant in grants:
        row = await db.get(_ACCOUNT_TYPES[grant.resource_type], grant.resource_id)
        if row is None or (not isinstance(row, Credential) and row.credential_id is None):
            continue  # gone, or a tool/server without its owner's account
        found.append(
            {
                "resource_type": grant.resource_type,
                "resource_id": str(grant.resource_id),
                "name": row.name,
            }
        )
    return found


async def require_delegation_confirmed(
    db: AsyncSession, kind: ShareType, row: ShareRow, body: GrantCreate
) -> None:
    if kind.name != "agent" or body.confirm_delegation:
        return
    delegations = await delegated_accounts(db, row.id)
    if delegations:
        raise authorization_error("confirm_required", {"delegations": delegations})
