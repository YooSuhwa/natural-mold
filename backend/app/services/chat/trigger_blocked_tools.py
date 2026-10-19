"""Trigger-unsafe capabilities of a run's tree (plan 11.6; F-06 plan-aware split).

Moved from ``runtime_context``. With the run's ``RuntimeAccessPlan`` only the links the
plan allows are probed (a denied optional capability is excluded, not a failure); each
child is planned as a decided link (``root=False``) of the fixed trigger run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.authz.runtime_access_planner import RuntimeAccessPlan, RuntimeAccessPlanner
from app.authz.runtime_capability_scope import require_runtime_capability_tree
from app.tools.risk import trigger_blocked_tools

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.authz.org_context import OrgContext
    from app.models.agent import Agent


async def trigger_blocked_tools_for_agent_tree(
    agent: Agent,
    *,
    db: AsyncSession,
    context: OrgContext,
    plan: RuntimeAccessPlan | None = None,
) -> list[Any]:
    """Return trigger-unsafe capabilities for parent plus one-hop children."""
    from app.services.chat.runtime_context import build_agent_skills, build_tools_config

    if plan is None:
        require_runtime_capability_tree(agent, context)
    links = plan.sub_agent_links if plan is not None else agent.sub_agent_links
    blocked = trigger_blocked_tools(
        await build_tools_config(agent, context=context, db=db, conversation_id=None, plan=plan),
        has_agent_skills=bool(build_agent_skills(agent, context, plan)),
    )
    planner = RuntimeAccessPlanner(context, None)
    for link in links:
        child = link.sub_agent
        if child is None:
            continue
        child_plan = await planner.plan(child, root=False) if plan is not None else None
        child_tools_config = await build_tools_config(
            child, context=context, db=db, conversation_id=None, plan=child_plan
        )
        blocked.extend(
            trigger_blocked_tools(
                child_tools_config,
                has_agent_skills=bool(build_agent_skills(child, context, child_plan)),
            )
        )
    return blocked


__all__ = ["trigger_blocked_tools_for_agent_tree"]
