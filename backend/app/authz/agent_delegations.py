"""Delegation grants follow an agent's links (plan 10.6, D14, 7.2.1 "위임 연결·해제"; F-02).

``sync_agent_delegations(db, ctx, agent_id, added=...)`` is the single writer every link
path calls after it flushed its link changes (agent create/update, builder confirm,
blueprint create-agent, agent archive, the marketplace MCP snapshot refresh, Assistant
link tools):

* it grants ``agent:A consumer R`` (``delegation``) only for the objects of links ADDED
  in this call (``added``, from ``added_keys``): each tool, skill, subagent and model
  credential, and each MCP *server* (10.6; ``allowed_run_source`` over every run source,
  the 7.1 default). Kept links without a grant are left for the F-03 validator;
* it revokes link-derived grants whose object no link uses any more; one another link
  still uses stays (10.6 "같은 자원을 다른 연결이 쓰고 있으면 유지"). Grants of another
  organization are skipped, never raised on;
* objects outside the actor's organization/company (hidden B-06 links) or archived
  agents get no grant (D21).

``delegated_by`` is stamped where link rows are created (the callers' builders) and
``llm_credential_delegated_by`` wherever the model credential changes. Lock order
(E-02): ``lock_delegations`` takes the organization row (claimed for the Task 10
tracker) before any link, agent or grant write — callers lock first, sync re-locks only
when it writes (an archive with nothing to revoke keeps the pinned B08 order).

Writes go through ``GrantService`` (grant rows, outbox rows and ``delegation.*`` audit
in the caller's transaction) in every mode, legacy included (precedent: E-01 shares);
the queued outbox ids ride on the session and ``commit_and_apply`` writes them to
OpenFGA right after commit. In legacy there is no tuple writer, so the outbox
accumulates and drains when the mode is switched (C-05 worker / bootstrap). Only grants
whose ``source_link.link`` names a link source are managed here (m86 rows included).
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_transaction_tracker import issue_claim
from app.authz.grants import GrantRequest, GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.authz.organization_execution_prefix import mark_org_writer
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.runtime import runtime_authz
from app.authz.service_core import AuthzDecisions
from app.authz.tuple_loaders import resource_scope
from app.authz.tuple_types import RUN_SOURCE_CONDITION, RUN_SOURCES
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.authz import ResourceGrant
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.organization import Organization
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink

Key = tuple[str, UUID]
LINK_SOURCES = {
    "tool": "agent_tools",
    "mcp_server": "agent_mcp_tools",
    "skill": "agent_skills",
    "agent": "agent_subagents",
    "credential": "agents.llm_credential_id",
}
_PENDING = "authz.delegation_outbox"
_LINK_TYPES = (AgentToolLink, AgentMcpToolLink, AgentSkillLink, AgentSubAgentLink)


async def linked_objects(
    db: AsyncSession, agent_id: UUID, kinds: Iterable[str] = LINK_SOURCES
) -> set[Key]:
    """The ``kinds`` objects ``agent_id``'s current links delegate (MCP tools by server)."""
    found: set[Key] = set()
    queries = (
        ("tool", select(AgentToolLink.tool_id).where(AgentToolLink.agent_id == agent_id)),
        (
            "mcp_server",
            select(McpTool.server_id)
            .join(AgentMcpToolLink, AgentMcpToolLink.mcp_tool_id == McpTool.id)
            .where(AgentMcpToolLink.agent_id == agent_id),
        ),
        ("skill", select(AgentSkillLink.skill_id).where(AgentSkillLink.agent_id == agent_id)),
        (
            "agent",
            select(AgentSubAgentLink.sub_agent_id).where(
                AgentSubAgentLink.parent_agent_id == agent_id
            ),
        ),
        (
            "credential",
            select(Agent.llm_credential_id).where(
                Agent.id == agent_id, Agent.llm_credential_id.is_not(None)
            ),
        ),
    )
    for kind, query in queries:
        if kind not in kinds:
            continue
        found |= {(kind, rid) for rid in await db.scalars(query) if rid is not None}
    return found


async def link_grants(db: AsyncSession, agent_ids: set[UUID]) -> list[ResourceGrant]:
    """Active link-derived delegation grants naming one of ``agent_ids``."""
    if not agent_ids:
        return []
    rows = await db.scalars(
        select(ResourceGrant)
        .where(
            ResourceGrant.principal_type == "agent",
            ResourceGrant.principal_id.in_(agent_ids),
            ResourceGrant.grant_kind == "delegation",
            ResourceGrant.state == "active",
            ResourceGrant.resource_type.in_(LINK_SOURCES),
        )
        .order_by(ResourceGrant.resource_type, ResourceGrant.resource_id)
    )
    return [g for g in rows if (g.source_link or {}).get("link") in LINK_SOURCES.values()]


def delegation_request(kind: str, agent_id: UUID) -> GrantRequest:
    mcp = kind == "mcp_server"
    return GrantRequest(
        "consumer",
        "agent",
        agent_id,
        grant_kind="delegation",
        condition_name=RUN_SOURCE_CONDITION if mcp else None,
        condition_context={"allowed_sources": list(RUN_SOURCES)} if mcp else None,
        source_link={"link": LINK_SOURCES[kind]},
    )


async def added_keys(
    db: AsyncSession,
    *,
    tool_ids: Iterable[UUID] = (),
    mcp_tool_ids: Iterable[UUID] = (),
    skill_ids: Iterable[UUID] = (),
    agent_ids: Iterable[UUID] = (),
    credential_ids: Iterable[UUID] = (),
) -> set[Key]:
    """The delegated objects of newly created links (MCP tools resolve to their server)."""
    keys = {("tool", i) for i in tool_ids} | {("skill", i) for i in skill_ids}
    keys |= {("agent", i) for i in agent_ids} | {("credential", i) for i in credential_ids}
    mcp = set(mcp_tool_ids)
    if mcp:
        with db.no_autoflush:
            servers = await db.scalars(select(McpTool.server_id).where(McpTool.id.in_(mcp)))
        keys |= {("mcp_server", i) for i in servers}
    return keys


async def claim_new_links(db: AsyncSession, actor: UUID) -> set[Key]:
    """Pending (unflushed) link rows of this call: stamp ``delegated_by`` = ``actor`` and
    return their delegated objects for ``sync_agent_delegations(added=...)``."""
    found = [row for row in db.new if isinstance(row, _LINK_TYPES)]
    for row in found:
        if row.delegated_by is None:
            row.delegated_by = actor
    return await added_keys(
        db,
        tool_ids=[r.tool_id for r in found if isinstance(r, AgentToolLink)],
        mcp_tool_ids=[r.mcp_tool_id for r in found if isinstance(r, AgentMcpToolLink)],
        skill_ids=[r.skill_id for r in found if isinstance(r, AgentSkillLink)],
        agent_ids=[r.sub_agent_id for r in found if isinstance(r, AgentSubAgentLink)],
    )


async def lock_delegations(db: AsyncSession, ctx: OrgContext) -> None:
    """E-02 lock order: the organization row (``sharing.lock_org_row``'s lock) before
    link/agent/grant writes; pending ORM changes stay unflushed until after it. The lock
    carries an execution-tracker claim so it does not poison a live execution proof or a
    later canonical prefix (Task 10); inside the prefix it upgrades its ``FOR SHARE``."""
    query = (
        select(Organization.id)
        .where(Organization.id == ctx.org_id)
        .with_for_update()
        .execution_options(**issue_claim(db))
    )
    mark_org_writer(db)  # any later canonical prefix in this session takes it exclusively
    with db.no_autoflush:
        await db.execute(query)


async def sync_agent_delegations(
    db: AsyncSession,
    ctx: OrgContext,
    agent_id: UUID,
    *,
    added: Iterable[Key] = (),
    kinds: frozenset[str] = frozenset(LINK_SOURCES),
    service: GrantService | None = None,
) -> LifecycleChange:
    """Grant ``added`` objects still linked, revoke unlinked ones (see module doc)."""
    if db.sync_session.info.get("authz.runtime_link_view"):  # F-03 broken_links view
        raise RuntimeError("delegations synced on a session with the runtime link view")
    await db.flush()
    wanted = await linked_objects(db, agent_id, kinds)  # ``kinds``: only these link types
    found = await link_grants(db, {agent_id})
    held = {(g.resource_type, g.resource_id): g for g in found if g.resource_type in kinds}
    stale = [k for k in held.keys() - wanted if held[k].org_id == ctx.org_id]  # foreign: skip
    new = [k for k in (set(added) & wanted) - held.keys() if await _grantable(db, ctx, k)]
    if not stale and not new:
        return LifecycleChange()  # nothing to write: no lock (keeps the B08 archive order)
    await lock_delegations(db, ctx)  # before any grant write (re-lock when callers took it)
    grants = service or GrantService()
    outbox: list[UUID] = []
    for key in sorted(stale, key=_order):
        outbox += (await grants.revoke(db, ctx, held[key].id)).outbox_ids
    for kind, rid in sorted(new, key=_order):
        change = await grants.grant(
            db, ctx, GrantTarget(kind, rid), delegation_request(kind, agent_id)
        )
        outbox += change.outbox_ids
    db.sync_session.info.setdefault(_PENDING, []).extend(outbox)
    return LifecycleChange(tuple(outbox))


async def _grantable(db: AsyncSession, ctx: OrgContext, key: Key) -> bool:
    """Delegations stay inside the organization (D21): a link to an object of another
    organization/company (a hidden physical link, B-06) or to an archived agent gets no
    grant; the delegation validator (F-03) reports such links."""
    scope = await resource_scope(db, *key)
    return (
        scope is not None
        and not scope.archived
        and scope.tenant_id in (None, ctx.tenant_id)
        and scope.org_id in (None, ctx.org_id)
    )


def _order(key: Key) -> tuple[str, bytes]:
    return key[0], key[1].bytes


def pending_change(db: AsyncSession, *changes: LifecycleChange) -> LifecycleChange:
    """``changes`` plus the delegation outbox rows queued on this session (and clears them)."""
    queued = db.sync_session.info.pop(_PENDING, [])
    ids = [i for change in changes for i in change.outbox_ids] + list(queued)
    return LifecycleChange(tuple(dict.fromkeys(ids)))


async def commit_and_apply(
    db: AsyncSession, authz: AuthzDecisions | None = None, *changes: LifecycleChange
) -> None:
    """Commit, then write ``changes`` and the queued delegation rows (8.6 item 2)."""
    change = pending_change(db, *changes)
    await db.commit()
    await apply_lifecycle(change, authz or runtime_authz())
