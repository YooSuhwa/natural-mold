"""Scheduler entrypoint — executes a single trigger run.

Greenfield M5: pulls everything via ``chat_service.get_agent_with_tools`` so
prefetch matches the conversations router exactly. The legacy
``llm_provider`` join + Fernet decrypt is replaced by the same user-owned
credential resolver used by conversations. Env/system credentials stay
reserved for internal service flows.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.agent_stream_runner import execute_agent_invoke
from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.agent_runtime.identity import (
    AgentIdentityError,
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.agent_runtime.run_secrets import collect_cfg_secret_values
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.agent_runtime.subagents import build_subagents_config
from app.agent_runtime.trigger_execution_claim import claim_trigger_run
from app.agent_runtime.trigger_execution_delivery import deliver_trigger_success
from app.agent_runtime.trigger_execution_invoke import invoke_trigger_run
from app.agent_runtime.trigger_execution_materialization import (
    TriggerOutcome,
    materialize_trigger_run,
)
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.trigger_scope import require_trigger_actor
from app.database import SessionTenant, async_session, platform_session, tenant_session
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.model import Model
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service, trigger_service
from app.tools.risk import format_trigger_block_reason

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _TriggerSubagentBuild:
    agent: Agent
    db: AsyncSession
    config: AgentConfig


@dataclass(frozen=True, slots=True)
class _TriggerInvocation:
    config: AgentConfig
    input_message: str
    run_id: uuid.UUID


async def _configure_trigger_subagents(request: _TriggerSubagentBuild) -> None:
    subagents, display_names = await build_subagents_config(
        request.agent,
        db=request.db,
        parent_cfg=request.config,
        is_trigger_mode=True,
    )
    request.config.subagents_config = subagents
    request.config.subagent_display_names = display_names


async def _invoke_trigger(request: _TriggerInvocation) -> str:
    return await execute_agent_invoke(
        request.config,
        [{"role": "user", "content": request.input_message}],
        run_id=str(request.run_id),
        moldy_source="trigger",
    )


async def _resolve_fallback_chain(db, fallback_list):
    """Resolve ``Agent.model_fallback_list`` (UUID strings) into chain dicts.

    Mirrors the conversations router helper so trigger runs and chat use the
    same resolution rules: missing rows are silently dropped.
    """

    if not fallback_list:
        return None
    fallback_uuids: list[uuid.UUID] = []
    for raw in fallback_list:
        try:
            fallback_uuids.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not fallback_uuids:
        return None
    result = await db.execute(select(Model).where(Model.id.in_(fallback_uuids)))
    rows = {row.id: row for row in result.scalars().all()}
    chain = []
    for fid in fallback_uuids:
        row = rows.get(fid)
        if row is None:
            continue
        chain.append(
            {
                "provider": row.provider,
                "model_name": row.model_name,
                "base_url": row.base_url,
                "model_id": str(row.id),
            }
        )
    return chain or None


# ``_resolve_llm_api_key`` was inlined here; the shared resolver in
# ``app.agent_runtime.credential_resolution.resolve_llm_api_key_for_agent``
# now owns the policy (agent.llm_credential → model.default_credential_id →
# None).


async def _trigger_tenant(trigger_uuid: uuid.UUID) -> SessionTenant | None:
    """8.9: the scheduler resolves the trigger row's company, then runs in it."""
    async with platform_session("scheduler.trigger_dispatch", maker=async_session) as db:
        row = (
            await db.execute(
                select(AgentTrigger.tenant_id, AgentTrigger.user_id).where(
                    AgentTrigger.id == trigger_uuid
                )
            )
        ).one_or_none()
    return None if row is None else SessionTenant(row.tenant_id, row.user_id)


async def execute_trigger(trigger_id: str, *, force: bool = False) -> AgentTriggerRun | None:
    """Called by APScheduler when a trigger fires."""

    trigger_uuid = uuid.UUID(trigger_id)
    tenant = await _trigger_tenant(trigger_uuid)

    # A missing trigger keeps an unbound session; the claim logs and skips it.
    async with tenant_session(tenant, maker=async_session) as db:
        claim = await claim_trigger_run(db, trigger_id, trigger_uuid, force=force)
        if claim is None or isinstance(claim, AgentTriggerRun):
            return claim  # skipped, or BLOCKED at dispatch (F-08)
        materialized = await materialize_trigger_run(db, trigger_id, claim, force=force)
        if isinstance(materialized, TriggerOutcome):
            return materialized.run
        invoked = await invoke_trigger_run(db, trigger_id, materialized)
        if isinstance(invoked, TriggerOutcome):
            return invoked.run
        return await deliver_trigger_success(db, trigger_id, materialized, invoked.output)


__all__ = ["AgentCredentialAccess", "resolve_llm_api_key_for_agent", "AgentIdentityError"]
__all__ += ["AgentRunSource", "make_agent_runtime_name", "resolve_agent_run_identity"]
__all__ += ["collect_cfg_secret_values", "RuntimePolicySnapshotError", "AgentExecutionTarget"]
__all__ += ["load_agent_tree_for_execution", "require_trigger_actor", "AppError", "AgentTrigger"]
__all__ += ["ConversationPolicyTarget", "chat_service", "trigger_service"]
__all__ += ["format_trigger_block_reason"]
