"""Locked trigger revalidation and trigger-mode config for prepared leases (Task 10, I18).

``lock_trigger_envelope`` runs first in the materialization session: the trigger
row read during the claim transaction is only an ID hint, so this re-enters the
canonical gateway (Tenant -> Organization -> owner memberships -> User -> Agent
lifecycle), takes the ``AgentTrigger`` lock (class ``TRIGGER``, before the
Conversation/Run locks ``materialize_and_activate`` adds next) and revalidates
everything another backend may have changed since the envelope was accepted.
It returns the exact trigger envelope input (the accepted ``AgentTriggerRun``
input message) as the lease's message history.

``trigger_config_resolver`` is the ``ResolveConfig`` the prepared-run adapter
calls under those same locks: it re-reads the agent and rebuilds the trigger
``AgentConfig`` exactly as the legacy invoke path does (shared
``build_trigger_agent_config``), with trigger-unsafe tools re-checked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import credential_resolution
from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.identity import (
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.agent_runtime.message_utils import RuntimeMessage
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.trigger_agent_config import TriggerConfigSource, build_trigger_agent_config
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.execution_lock_cursor import LockClass
from app.authz.organization_execution_gateway import acquire_execution_proof, lock_row
from app.authz.scoping import private_scope_predicate
from app.error_codes import agent_not_found
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service
from app.services.agent_execution_runtime import RuntimeOwnershipRequest
from app.services.authorized_runtime import prepare_authorized_runtime
from app.services.conversation_stream_service import resolve_fallback_chain
from app.services.prepared_run_adapter import ResolveConfig
from app.services.trigger_conversation_runtime import ensure_schedule_conversation_runtime_policy

STALE = "prepared_trigger_stale"


@dataclass(frozen=True, slots=True)
class PreparedTriggerTarget:
    """The accepted trigger envelope a prepared trigger run materializes."""

    trigger_id: uuid.UUID
    trigger_run_id: uuid.UUID
    force: bool
    ownership: RuntimeOwnershipRequest


class PreparedTriggerStaleError(RuntimeError):
    """The trigger/envelope changed after acceptance; fail before the marker."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.code = f"{STALE}:{reason}"
        super().__init__(self.code)


class PreparedTriggerFailedError(RuntimeError):
    """Pre-activation failure already recorded on the run; the trigger run fails."""

    def __init__(self, message: str) -> None:
        self.code = message
        super().__init__(message)


class PreparedTriggerTerminalError(RuntimeError):
    """Pre-activation outcome the trigger run records as ``status`` (skipped/canceled)."""

    def __init__(self, status: str, message: str | None) -> None:
        self.status, self.message = status, message
        super().__init__(f"{status}: {message}")


async def lock_trigger_envelope(
    db: AsyncSession, target: PreparedTriggerTarget
) -> list[RuntimeMessage]:
    """Gateway, agent tree, then the locked trigger reload; return the envelope input."""

    ownership = target.ownership
    context, agent_id = ownership.context, ownership.root_agent_id
    proof = await acquire_execution_proof(db, context, root_agent_id=agent_id)
    # F-08 (11.1): like the claim, a child of another member is the planner's decision.
    await load_agent_tree_for_execution(db, AgentExecutionTarget(agent_id, context, run=True))
    trigger = await lock_row(
        db,
        proof.cursor,
        LockClass.TRIGGER,
        target.trigger_id,
        select(AgentTrigger)
        .where(
            AgentTrigger.id == target.trigger_id,
            AgentTrigger.agent_id == agent_id,
            private_scope_predicate(AgentTrigger, context),
        )
        .with_for_update()
        .execution_options(populate_existing=True),
    )
    if trigger is None:
        raise PreparedTriggerStaleError("trigger_missing")
    if trigger.status != "active" and not target.force:
        raise PreparedTriggerStaleError("trigger_inactive")
    envelope = await db.scalar(
        select(AgentTriggerRun)
        .where(
            AgentTriggerRun.id == target.trigger_run_id,
            AgentTriggerRun.trigger_id == trigger.id,
            AgentTriggerRun.conversation_run_id == ownership.run_id,
        )
        .execution_options(populate_existing=True)
    )
    if envelope is None or envelope.status != "queued":
        raise PreparedTriggerStaleError("envelope_not_queued")
    if envelope.input_message != trigger.input_message:
        raise PreparedTriggerStaleError("input_changed")
    return [{"role": "user", "content": envelope.input_message}]


def trigger_config_resolver(target: PreparedTriggerTarget) -> ResolveConfig:
    """Rebuild the trigger-mode root ``AgentConfig`` from rows read under the locks."""

    context = target.ownership.context

    async def resolve(db: AsyncSession, run: ConversationRun) -> AgentConfig:
        agent = await chat_service.get_agent_with_tools(db, target.ownership.root_agent_id, context)
        if agent is None:
            raise agent_not_found()
        if agent.model is None:
            raise PreparedTriggerStaleError("agent_model_missing")
        identity = resolve_agent_run_identity(
            agent_id=agent.id,
            agent_owner_user_id=agent.user_id,
            runtime_name=agent.runtime_name or make_agent_runtime_name(agent.id),
            identity_mode=agent.identity_mode,
            source=AgentRunSource.TRIGGER,
            caller_user_id=None,
        )
        runtime = await prepare_authorized_runtime(
            agent, path="trigger", context=context, db=db, identity=None
        )
        plan = runtime.plan
        if await chat_service.trigger_blocked_tools_for_agent_tree(
            agent, context=context, db=db, plan=plan
        ):
            raise PreparedTriggerStaleError("trigger_blocked_tools")
        runtime_policy = await ensure_schedule_conversation_runtime_policy(
            db, ConversationPolicyTarget(run.conversation_id, context, target.trigger_run_id)
        )
        api_key = await credential_resolution.resolve_llm_api_key_for_agent(
            db, agent, AgentCredentialAccess(context, identity)
        )
        return build_trigger_agent_config(
            TriggerConfigSource(
                agent=agent,
                context=context,
                identity=identity,
                effective_prompt=chat_service.build_effective_prompt(agent),
                agent_skills=runtime.agent_skills,
                tools_config=runtime.tools_config,
                thread_id=str(run.conversation_id),
                runtime_policy=runtime_policy,
                api_key=api_key,
                fallback_chain=await resolve_fallback_chain(db, agent.model_fallback_list),
                access_plan=plan,
            )
        )

    return resolve
