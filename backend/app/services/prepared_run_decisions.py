"""Decide a prepared run's authorization before its locks (plan 8.10, 11.2; F-06, I17).

``predecided_run`` opens a fresh run decision memo, loads the agent tree without locks
in its own session and replays the boundary's decisions there: the caller's
``agent#can_run`` (shared root), the root and child ``RuntimeAccessPlan`` and the LLM
credential delegation. Then it seals the memo, so the locked resolver
(``materialize_and_activate``) reuses those answers, re-validates only DB state and
never calls OpenFGA while the materialization locks are held; a decision it did not
make there is a denial (fail closed). Denials here are not raised: the locked pass
reaches the same answer from the memo inside the lease's own failure handling.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.credential_resolution import llm_credential_delegated
from app.agent_runtime.identity import (
    AgentIdentityError,
    AgentRunIdentity,
    AgentRunSource,
    derive_child_agent_run_identity,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.authz.org_context import OrgContext
from app.authz.run_decisions import RunDecisionMemo, fresh_run_decision_scope
from app.authz.runtime_access_planner import RuntimeAccessPlanner, runtime_access_enforced
from app.database import tenant_session
from app.exceptions import AppError
from app.models.agent import Agent


def _identity(agent: Agent, source: AgentRunSource, caller: uuid.UUID | None) -> AgentRunIdentity:
    return resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=agent.runtime_name or make_agent_runtime_name(agent.id),
        identity_mode=agent.identity_mode,
        source=source,
        caller_user_id=caller,
    )


async def _decide_agent(
    agent: Agent,
    context: OrgContext,
    identity: AgentRunIdentity | None,
    llm_identity: AgentRunIdentity,
    *,
    root: bool,
) -> tuple[Agent, ...]:
    """One agent's plan and LLM delegation; returns the children the plan admits."""
    plan = await RuntimeAccessPlanner(context, identity).plan(agent, root=root)
    credential = agent.llm_credential
    if credential is not None and credential.user_id != llm_identity.credential_subject_user_id:
        access = AgentCredentialAccess(context, llm_identity)
        await llm_credential_delegated(credential, agent, access)
    return tuple(link.sub_agent for link in plan.sub_agent_links if link.sub_agent is not None)


async def _predecide(
    db: AsyncSession,
    context: OrgContext,
    root_agent_id: uuid.UUID,
    source: AgentRunSource | None,
    caller: uuid.UUID | None,
) -> None:
    from app.services import chat_service

    agent = await chat_service.get_run_agent_with_tools(db, root_agent_id, context)
    if agent is None:
        return
    identity = _identity(agent, source, caller) if source is not None else None
    parent = identity or _identity(agent, AgentRunSource.TRIGGER, None)
    children = await _decide_agent(agent, context, identity, parent, root=True)
    for child in children:
        try:
            child_identity = derive_child_agent_run_identity(parent, child)
        except AgentIdentityError:
            continue  # the locked pass raises the same identity error
        await _decide_agent(child, context, child_identity, child_identity, root=False)


@asynccontextmanager
async def predecided_run(
    context: OrgContext,
    root_agent_id: uuid.UUID,
    *,
    source: AgentRunSource | None,
    caller_user_id: uuid.UUID | None = None,
) -> AsyncIterator[RunDecisionMemo]:
    """``source`` ``None``: a trigger (fixed, as the owner); chat passes the caller."""
    from app.services import agent_execution_runtime  # call time: the lease's session maker

    with fresh_run_decision_scope() as memo:
        if runtime_access_enforced():
            async with tenant_session(context, maker=agent_execution_runtime.async_session) as db:
                try:
                    await _predecide(db, context, root_agent_id, source, caller_user_id)
                except AppError:
                    pass  # recorded in the memo (or ``unavailable``); the locked pass answers
                finally:
                    await db.rollback()
        memo.seal()
        yield memo


__all__ = ["predecided_run"]
