"""D17 attach rule and the shared-agent re-check (plan D17, Q13, 10.6, 10.4; E-06).

* ``require_attachable``: attaching an account-carrying resource to an agent — a
  credential, or a tool / MCP server holding a credential (``shared`` mode, D15) — needs
  that resource's ``can_share`` unless the actor owns it in this organization. Platform
  rows (``is_system``) are catalog resources reached through fanout (E-03) and keep the
  plain 10.6 check. Callers pass only the resources being added (existing links were
  decided when they were made). Legacy: every attach path loads its candidates
  owner-scoped (or platform), so a non-owned row never gets here and the ``not_found``
  verdict only backs that up; OpenFGA decides once the type is enforced. Other resources
  keep their 10.6 ``can_use`` / ``can_run`` / ``can_invoke`` check.
* ``require_shared_attach``: when the links of an agent that is shared — directly, or as
  a subagent (at any depth) of a shared agent — change, the account carriers it gained
  are lent to its recipients: a personal account is refused (``409
  personal_account_shared``, 10.4) and the rest need confirmation (``409
  confirm_required`` with ``details.delegations``) unless ``confirm``, as when sharing
  (E-01). Carriers the agent already had are not asked again. Callers snapshot
  ``attach_baseline`` before changing links and call this after flushing them.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import AGENT_DELEGATION_CONFIRM
from app.authz.delegation_accounts import describe
from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.personal_account_carriers import Carrier, agent_carriers, with_parents
from app.authz.personal_accounts import has_share_grants, personal_of
from app.authz.principal import Principal
from app.authz.runtime import runtime_authz
from app.authz.service_types import Verdict
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.tool import Tool
from app.services import audit_service

ATTACH_PERMISSION = "can_share"
AccountRow = Tool | McpServer | Credential


async def _account_rows(
    db: AsyncSession,
    tool_ids: set[UUID],
    mcp_tool_ids: set[UUID],
    credential_ids: set[UUID],
) -> list[tuple[str, AccountRow]]:
    found: list[tuple[str, AccountRow]] = []
    if tool_ids:
        tools = await db.scalars(
            select(Tool).where(
                Tool.id.in_(tool_ids), Tool.credential_id.is_not(None), Tool.is_system.is_(False)
            )
        )
        found += [("tool", tool) for tool in tools]
    if mcp_tool_ids:
        servers = await db.scalars(
            select(McpServer)
            .join(McpTool, McpTool.server_id == McpServer.id)
            .where(
                McpTool.id.in_(mcp_tool_ids),
                McpServer.credential_id.is_not(None),
                McpServer.is_system.is_(False),
            )
            .distinct()
        )
        found += [("mcp_server", server) for server in servers]
    if credential_ids:
        credentials = await db.scalars(
            select(Credential).where(
                Credential.id.in_(credential_ids), Credential.is_system.is_(False)
            )
        )
        found += [("credential", credential) for credential in credentials]
    return found


def _owns(row: AccountRow, ctx: OrgContext) -> bool:
    return (
        row.user_id == ctx.user_id and row.org_id == ctx.org_id and row.tenant_id == ctx.tenant_id
    )


async def require_attachable(
    db: AsyncSession,
    ctx: OrgContext,
    *,
    tool_ids: Iterable[UUID] = (),
    mcp_tool_ids: Iterable[UUID] = (),
    credential_ids: Iterable[UUID] = (),
) -> None:
    """D17: the actor needs ``can_share`` on every non-owned account resource it adds."""
    with db.no_autoflush:
        rows = await _account_rows(db, set(tool_ids), set(mcp_tool_ids), set(credential_ids))
    foreign = sorted(
        ((kind, row) for kind, row in rows if not _owns(row, ctx)),
        key=lambda item: (item[0], item[1].id.bytes),
    )
    if not foreign:
        return  # owned or platform rows only: no decision to make (always so in legacy)
    authz = runtime_authz()
    for kind, row in foreign:
        await authz.require(
            Principal.for_user(ctx),
            ATTACH_PERMISSION,
            f"{kind}:{row.id}",
            min_visible="can_use",
            resource_org_id=row.org_id,
            legacy=Verdict.NOT_FOUND,
        )


async def attach_baseline(db: AsyncSession, agent_id: UUID) -> frozenset[Carrier]:
    """The account carriers ``agent_id`` lends before its links change."""
    return frozenset(await agent_carriers(db, agent_id))


async def require_shared_attach(
    db: AsyncSession,
    ctx: OrgContext,
    agent_id: UUID,
    before: frozenset[Carrier],
    *,
    confirm: bool,
) -> list[Carrier]:
    """Re-run the 10.4 refusal and the D17 confirmation for carriers the agent gained.

    Returns the carriers a shared agent now lends (the caller audits a confirmation)."""
    added = [c for c in await agent_carriers(db, agent_id) if c not in before]
    if not added:
        return []
    if not await has_share_grants(db, ctx.org_id, "agent", await with_parents(db, [agent_id])):
        return []
    personal = await personal_of(db, added)
    if personal:
        raise authorization_error("personal_account_shared", {"personal_accounts": personal})
    delegations = [] if confirm else await describe(db, added)
    if delegations:
        raise authorization_error("confirm_required", {"delegations": delegations})
    return sorted(added, key=lambda c: (c[0], str(c[1]), str(c[2])))


async def audit_delegation_confirm(
    db: AsyncSession, ctx: OrgContext, agent: Agent, lent: list[Carrier]
) -> None:
    """``agent.delegation_confirm``: which accounts the actor confirmed (ids only, no secrets)."""
    delegations = [
        {"resource_type": kind, "resource_id": str(rid), "credential_id": str(cid)}
        for kind, rid, cid in lent
    ]
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=ctx.user_id,
        owner_user_id=agent.user_id,
        action=AGENT_DELEGATION_CONFIRM,
        target_type="agent",
        target_id=agent.id,
        target_name_snapshot=agent.name,
        target_owner_user_id=agent.user_id,
        outcome="success",
        metadata={"delegations": delegations},
    )
