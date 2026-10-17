"""Runs skip broken links and refuse when a required one is broken (plan 10.6, 12.2; F-03).

"끊긴 연결은 실행 시 제외되거나(선택 항목) 실행을 막습니다(필수 항목)":

* ``use_runtime_link_view(db)``: from then on every ORM SELECT on this session (and the
  eager/lazy loads it drives) sees only ``link_status='active'`` rows of the four agent
  link tables (``with_loader_criteria``), so run preparation never binds a broken tool,
  MCP tool, skill or subagent. Run-prep entry points call it before they load the agent;
  edit/read paths never do, so editors keep seeing broken links, and link writers
  refuse a session that has it (``assert_no_runtime_view``). Links are only ever
  broken in enforce mode (F-03 validator), so legacy loads are unchanged.
* ``require_runnable_links(db, agent_id)``: ``409 agent_not_ready`` (12.2 ``blocked``,
  items ``result=delegation_broken``) when a ``required`` link of the agent, or of a
  subagent it reaches through an active link (one hop, 10.6), is broken.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.authz.error_codes import agent_not_ready
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink

RUNTIME_VIEW = "authz.runtime_link_view"
INCLUDE_BROKEN = "authz_include_broken"  # execution option: see broken links anyway
_LINKS: tuple[tuple[str, Any, Any, Any], ...] = (
    ("tool", AgentToolLink, AgentToolLink.agent_id, AgentToolLink.tool_id),
    ("mcp_tool", AgentMcpToolLink, AgentMcpToolLink.agent_id, AgentMcpToolLink.mcp_tool_id),
    ("skill", AgentSkillLink, AgentSkillLink.agent_id, AgentSkillLink.skill_id),
    (
        "agent",
        AgentSubAgentLink,
        AgentSubAgentLink.parent_agent_id,
        AgentSubAgentLink.sub_agent_id,
    ),
)


def use_runtime_link_view(db: AsyncSession) -> None:
    """Hide broken links from every later load on this session (run preparation only)."""
    db.sync_session.info[RUNTIME_VIEW] = True


@event.listens_for(Session, "do_orm_execute")
def _active_links_only(state: ORMExecuteState) -> None:
    if not state.session.info.get(RUNTIME_VIEW) or not state.is_select:
        return
    if state.is_column_load or state.is_relationship_load:
        return  # criteria added to the top-level statement propagate to these loads
    if state.execution_options.get(INCLUDE_BROKEN):
        return
    state.statement = state.statement.options(
        *(
            with_loader_criteria(model, model.link_status == "active", include_aliases=True)
            for _, model, _, _ in _LINKS
        )
    )


def has_broken_links(agent: Agent) -> bool:
    """A loaded agent (or a loaded one-hop child) still holds a broken link row."""
    tree = [agent] + [
        link.sub_agent
        for link in _loaded(agent, "sub_agent_links")
        if "sub_agent" not in inspect(link).unloaded
    ]
    names = ("tool_links", "mcp_tool_links", "skill_links", "sub_agent_links")
    return any(
        link.link_status == "broken"
        for node in tree
        if node is not None
        for name in names
        for link in _loaded(node, name)
    )


def _loaded(node: Agent, name: str) -> list[Any]:
    """Only collections already loaded: never a lazy load (async sessions)."""
    return [] if name in inspect(node).unloaded else list(getattr(node, name))


def assert_no_runtime_view(db: AsyncSession) -> None:
    """Link writers must never run on a run-prep session: the view hides broken rows."""
    if db.sync_session.info.get(RUNTIME_VIEW):
        raise RuntimeError("agent links written on a session with the runtime link view")


async def require_runnable_links(db: AsyncSession, agent_id: UUID) -> None:
    """``409 agent_not_ready`` when a required link in the one-hop tree is broken."""
    items = await broken_required(db, agent_id)
    if items:
        # F-06 L2: the runner may not see the linked rows, so the response carries no ids.
        shown = [{key: value for key, value in item.items() if key != "id"} for item in items]
        raise agent_not_ready(shown)


def blocked_reason(items: list[dict[str, object]]) -> str:
    """Failed trigger-run ``error_message``: reason ``delegation_broken`` + the links."""
    parts = ", ".join(f"{item['kind']} {item['id']} of agent {item['agent_id']}" for item in items)
    return f"delegation_broken: required link unavailable ({parts})"


async def broken_required(db: AsyncSession, agent_id: UUID) -> list[dict[str, object]]:
    """Required broken links of the agent and of children reached by an active link."""
    with db.no_autoflush:
        children = list(
            await db.scalars(
                select(AgentSubAgentLink.sub_agent_id)
                .where(
                    AgentSubAgentLink.parent_agent_id == agent_id,
                    AgentSubAgentLink.link_status == "active",
                )
                .execution_options(**{INCLUDE_BROKEN: True})
            )
        )
        items: list[dict[str, object]] = []
        for kind, model, owner, target in _LINKS:
            rows = await db.execute(
                select(owner, target, model.broken_reason)
                .where(
                    owner.in_([agent_id, *children]),
                    model.required.is_(True),
                    model.link_status == "broken",
                )
                .order_by(owner, target)
                .execution_options(**{INCLUDE_BROKEN: True})
            )
            items += [
                {
                    "kind": kind,
                    "id": str(rid),
                    "agent_id": str(aid),
                    "required": True,
                    "result": "delegation_broken",
                    "reason": reason,
                }
                for aid, rid, reason in rows.all()
            ]
    return items
