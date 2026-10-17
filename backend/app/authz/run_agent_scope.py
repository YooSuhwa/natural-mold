"""Whose agent a run may resolve credentials for (plan 11.1; F-05).

The owner's own agent, or someone else's agent in the same organization only for its
authenticated caller holding ``agent#can_run`` (legacy: owner only, the existing 404).
Shared agent trees load in ``run_agent_loader`` and their links are decided by
``RuntimeAccessPlanner`` (F-06).
"""

from typing import Literal

from app.agent_runtime.identity import AgentRunIdentity
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.run_decisions import require_decided
from app.authz.runtime import runtime_authz
from app.authz.service import AuthzService
from app.authz.service_types import Verdict
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import Agent


async def require_runnable_agent(
    agent: Agent,
    context: OrgContext,
    identity: AgentRunIdentity | None,
    authz: AuthzService | None,
) -> None:
    """Same organization; someone else's agent only for its authenticated caller with
    ``agent#can_run`` (legacy: owner only, the existing 404)."""
    if (agent.org_id, agent.tenant_id) != (context.org_id, context.tenant_id):
        raise agent_not_found()
    if agent.user_id == context.user_id:
        return
    if identity is None or identity.caller_user_id != context.user_id:
        raise agent_not_found()
    await require_decided(
        authz or runtime_authz(),
        Principal.for_user(context),
        "can_run",
        f"agent:{agent.id}",
        min_visible="can_run",
        resource_org_id=agent.org_id,
        legacy=Verdict.NOT_FOUND,
        not_found=agent_not_found(),
    )


async def masking_scope(
    agent: Agent, context: OrgContext, identity: AgentRunIdentity
) -> Literal["own", "shared"] | None:
    """Response masking reads what the caller's run would resolve: their own agent, or a
    shared agent they may run. ``None`` (not runnable): heuristics-only masking."""
    if (agent.user_id, agent.org_id, agent.tenant_id) == (
        context.user_id,
        context.org_id,
        context.tenant_id,
    ):
        return "own"
    try:
        await require_runnable_agent(agent, context, identity, None)
    except AppError as denied:
        if denied.status in (403, 404):
            return None
        raise
    return "shared"


__all__ = ["masking_scope", "require_runnable_agent"]
