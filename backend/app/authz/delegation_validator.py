"""delegation_validator: "can the delegator still use it?" (plan 10.6, 8.6 위임 검증, D14; F-03).

``validate_delegations(db, scope, authz=...)`` re-checks every active delegated link in
``scope`` (``delegation_links``) for its delegator, in the organization:

* lost (an OpenFGA check fails, the delegator account is gone, or the object can no
  longer carry a delegation — another organization/company or archived): the link
  becomes ``link_status=broken`` with ``broken_reason`` (``delegator_lost_access`` /
  ``resource_unavailable``), audit ``delegation.broken`` (system actor, the agent as
  target), and its delegation grant is revoked unless another active link of the agent
  still uses the object (10.6). The model credential has no status column: its grant is
  revoked (the F-05 tier-1 check then refuses the run; the LLM is always required);
* kept (F-02 left links without a grant — MCP discovery, fixtures, m86 out-of-scope
  rows): a link whose delegator still passes gets its grant back (repair, granted by the
  delegator); restoring access never heals a broken link — an editor re-attaches it.

Only settled state is judged (``delegation_decisions``: pending markers and undelivered
outbox rows defer). Callers run this after commit + apply; finalized markers are
re-validated by the maintenance tick (``delegation_retry``) and the daily job is the
backstop. Each break/repair runs in its own savepoint: a failure is logged and skipped.

Modes: legacy never consults OpenFGA (no-op). A link is acted on only when every type
it touches is enforced; otherwise (shadow, partial enforce) it is only reported in
``unenforced``. ``dry_run`` reports without writing or locking. Lock order (E-02): the
organization row (``lock_delegations``) before links and grants are read; a break only
applies while the link still has the delegator that was judged.
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_delegations import delegation_request, link_grants, lock_delegations
from app.authz.audit_actions import DELEGATION_BROKEN
from app.authz.delegation_decisions import Reason, decide
from app.authz.delegation_links import DelegatedLink, DelegationScope, delegated_links
from app.authz.grants import GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.authz.resource_lifecycle import LifecycleChange
from app.authz.service_core import AuthzDecisions
from app.models.agent_subagent import AgentSubAgentLink
from app.models.authz import ResourceGrant
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.organization import Organization
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink
from app.services import audit_service

logger = logging.getLogger(__name__)
_COLUMNS = {
    "agent_tools": (AgentToolLink, AgentToolLink.agent_id, AgentToolLink.tool_id),
    "agent_mcp_tools": (AgentMcpToolLink, AgentMcpToolLink.agent_id, AgentMcpToolLink.mcp_tool_id),
    "agent_skills": (AgentSkillLink, AgentSkillLink.agent_id, AgentSkillLink.skill_id),
    "agent_subagents": (
        AgentSubAgentLink,
        AgentSubAgentLink.parent_agent_id,
        AgentSubAgentLink.sub_agent_id,
    ),
}


@dataclass(frozen=True, slots=True)
class BrokenLink:
    link: DelegatedLink
    reason: Reason


@dataclass(slots=True)
class DelegationReport:
    broken: list[BrokenLink] = field(default_factory=list)
    repaired: list[DelegatedLink] = field(default_factory=list)
    deferred: list[DelegatedLink] = field(default_factory=list)
    unenforced: list[BrokenLink] = field(default_factory=list)  # would break; not acted on
    failed: list[DelegatedLink] = field(default_factory=list)  # skipped, logged
    revoked_grant_ids: list[UUID] = field(default_factory=list)
    change: LifecycleChange = field(default_factory=LifecycleChange)


async def validate_delegations(
    db: AsyncSession,
    scope: DelegationScope,
    *,
    authz: AuthzDecisions,
    trigger: str,
    dry_run: bool = False,
    service: GrantService | None = None,
) -> DelegationReport:
    """Re-check ``scope``'s delegations (module doc); the caller commits and applies."""
    report = DelegationReport()
    org = await db.get(Organization, scope.org_id)
    if authz.mode == "legacy" or org is None:
        return report
    if not dry_run:  # E-02 order: the org row before any link/grant read we act on
        await lock_delegations(db, OrgContext(org.id, org.tenant_id, user_id=org.id))
    links = await delegated_links(db, scope, org.tenant_id)
    held = {
        (g.principal_id, g.resource_type, g.resource_id): g
        for g in await link_grants(db, {link.agent_id for link in links})
    }
    verdicts = await decide(db, authz, scope.org_id, links)
    for link in links:
        verdict, reason = verdicts[link]
        has_grant = (link.agent_id, *link.obj) in held
        if verdict == "deferred":
            report.deferred.append(link)
        elif not _acts(authz, link):
            if verdict == "lost":
                report.unenforced.append(BrokenLink(link, reason))
        elif verdict == "lost" and (has_grant or link.table in _COLUMNS):
            report.broken.append(BrokenLink(link, reason))
        elif verdict == "ok" and not has_grant and link.table in _COLUMNS:
            report.repaired.append(link)  # never the model credential: no silent re-grant
    if dry_run:
        return report
    grants = service or GrantService()
    outbox: list[UUID] = []
    for item in list(report.broken):
        try:
            async with db.begin_nested():  # one bad link must not stop the organization
                outbox += await _break(db, item, trigger, org.tenant_id, held, grants, report)
        except Exception:  # noqa: BLE001 - logged; retried by the next run
            logger.exception("delegation_break_failed", extra={"agent_id": str(item.link.agent_id)})
            report.broken.remove(item)
            report.failed.append(item.link)
    outbox += await _repair(db, org, report, grants)
    report.change = LifecycleChange(tuple(outbox))
    return report


def _acts(authz: AuthzDecisions, link: DelegatedLink) -> bool:
    types = {link.obj[0], *(obj.partition(":")[0] for _, obj in link.checks)}
    return all(authz.enforces(kind) for kind in types)


async def _break(
    db: AsyncSession,
    item: BrokenLink,
    trigger: str,
    tenant_id: UUID,
    held: dict[tuple[UUID, str, UUID], ResourceGrant],
    grants: GrantService,
    report: DelegationReport,
) -> list[UUID]:
    """Mark one link broken, audit it, and revoke its grant unless still used."""
    link = item.link
    columns = _COLUMNS.get(link.table)
    if columns is not None:
        model, agent_col, link_col = columns
        result = await db.execute(
            update(model)
            .where(agent_col == link.agent_id, link_col == link.link_id)
            .where(model.link_status == "active")  # type: ignore[attr-defined]
            .where(model.delegated_by.is_not_distinct_from(link.delegator))  # type: ignore[attr-defined]
            .values(link_status="broken", broken_reason=item.reason)
            .execution_options(synchronize_session=False)
        )
        if not getattr(result, "rowcount", 1):
            return []  # changed meanwhile (re-attached, broken, removed): not ours to break
    await audit_service.record_event(
        db,
        actor_type="system",
        tenant_id=tenant_id,  # platform sessions (daily job) carry no company scope
        action=DELEGATION_BROKEN,
        target_type="agent",
        target_id=link.agent_id,
        outcome="success",
        reason_code=item.reason,
        metadata={
            "link": link.table,
            "link_id": str(link.link_id),
            "resource_type": link.obj[0],
            "resource_id": str(link.obj[1]),
            "delegated_by": str(link.delegator) if link.delegator else None,
            "reason": item.reason,
            "trigger": trigger,
        },
    )
    grant = held.get((link.agent_id, *link.obj))
    if grant is None or await _still_linked(db, link):
        return []
    audit = {"reason": item.reason, "trigger": trigger, "link": link.table}
    closed = await grants.revoke_as_system(db, grant.id, audit=audit)  # re-read, locked
    if closed is None:
        return []
    report.revoked_grant_ids.append(grant.id)
    return list(closed.outbox_ids)


async def _still_linked(db: AsyncSession, link: DelegatedLink) -> bool:
    """Another active link of the agent still uses the object (MCP: same server)."""
    if link.table != "agent_mcp_tools":
        return False  # one link per (agent, object); the model credential has one too
    found = await db.scalar(
        select(AgentMcpToolLink.mcp_tool_id)
        .join(McpTool, McpTool.id == AgentMcpToolLink.mcp_tool_id)
        .where(
            AgentMcpToolLink.agent_id == link.agent_id,
            AgentMcpToolLink.link_status == "active",
            McpTool.server_id == link.obj[1],
        )
        .limit(1)
    )
    return found is not None


async def _repair(
    db: AsyncSession, org: Organization, report: DelegationReport, grants: GrantService
) -> list[UUID]:
    """Grant kept links that lack one, once per (agent, object), as their delegator."""
    outbox: list[UUID] = []
    done: set[tuple[UUID, str, UUID]] = set()
    for link in list(report.repaired):
        key = (link.agent_id, *link.obj)
        if key in done or link.delegator is None:
            continue
        done.add(key)
        ctx = OrgContext(org_id=org.id, tenant_id=org.tenant_id, user_id=link.delegator)
        request = delegation_request(link.obj[0], link.agent_id)
        try:
            async with db.begin_nested():
                change = await grants.grant(db, ctx, GrantTarget(*link.obj), request)
        except Exception:  # noqa: BLE001 - logged; retried by the next run
            logger.exception("delegation_repair_failed", extra={"agent_id": str(link.agent_id)})
            report.repaired.remove(link)
            report.failed.append(link)
            continue
        outbox += change.outbox_ids
    return outbox
