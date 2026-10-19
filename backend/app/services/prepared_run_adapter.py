"""Lease preparation adapter: ``prepare_inputs(db, run)`` for prepared runs (Task 10).

``PreparedRunResolver`` is the resolver ``materialize_and_activate`` invokes
after it has locked the queued run. It refuses to run unless the transaction's
execution proof holds that run's Run/Conversation locks, re-enters the agent
tree through ``load_agent_tree_for_execution`` (no lock SQL inside the proof;
any agent outside the proof is denied) and requires the Agent-lifecycle lock
for the root and every linked child. Every lookup then reads through that same
locked session: the existing ``AgentConfig`` path (``resolve_config``; chat
default = ``resolve_agent_context`` without legacy child compilation), the
root/child ``LeaseModelSpec`` (never the shared model cache), stored linked
children (``prepared_run_children``), the memory write policy and memory
prompt, and the snapshot inputs (``prepared_run_input_builder``). Unsupported
shapes fail with ``prepared_run_unsupported:<reason>`` before the marker.

After the call ``execution_cfg`` holds a secret-stripped copy of the config
for Langfuse/cost metadata only; it is never used to prepare anything.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import RuntimeMessage
from app.agent_runtime.prepared_run_input_builder import (
    PreparedRunSources,
    build_preparation_inputs,
)
from app.agent_runtime.prepared_run_input_policy import (
    lease_model_spec,
    require_lease_supported,
    unsupported,
)
from app.agent_runtime.prepared_run_inputs import PreparationInputs
from app.agent_runtime.runtime.memory_context import (
    load_memory_context_in,
    memory_write_policy_in,
)
from app.agent_runtime.runtime_config import AgentConfig, runtime_data_dir
from app.agent_runtime.runtime_preparation_bindings import _runtime_preparation_bindings
from app.agent_runtime.runtime_preparation_support import RuntimePreparationBindings
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.execution_lock_cursor import LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_proof import live_proof
from app.dependencies import CurrentUser
from app.error_codes import agent_not_found
from app.models.conversation_run import ConversationRun
from app.services import chat_service
from app.services.conversation_agent_context import resolve_agent_context
from app.services.prepared_run_children import resolve_prepared_children

ResolveConfig = Callable[[AsyncSession, ConversationRun], Awaitable[AgentConfig]]


def chat_config_resolver(
    user: CurrentUser, context: OrgContext, *, checkpoint_id: str | None = None
) -> ResolveConfig:
    """The chat/queue ``AgentConfig`` path, minus legacy child compilation."""

    async def resolve(db: AsyncSession, run: ConversationRun) -> AgentConfig:
        return await resolve_agent_context(
            db,
            run.conversation_id,
            user,
            context=context,
            checkpoint_id=checkpoint_id,
            include_subagents=False,
        )

    return resolve


@dataclass(frozen=True, slots=True)
class MemoryReads:
    """Memory policy/context reads bound to the resolver's locked session."""

    write_policy: Callable[..., Awaitable[str]] = memory_write_policy_in
    context: Callable[..., Awaitable[tuple[str, list[dict[str, Any]]]]] = load_memory_context_in


@dataclass(frozen=True, slots=True)
class PreparedRunRequest:
    context: OrgContext
    resolve_config: ResolveConfig
    run_root_base: Path
    messages_history: Sequence[RuntimeMessage] = ()
    is_trigger_mode: bool = False


def _require_locked(
    db: AsyncSession, run: ConversationRun, agent_ids: Collection[uuid.UUID]
) -> None:
    proof = live_proof(db)
    if (
        proof is None
        or not proof.cursor.holds(LockClass.RUN, run.id)
        or not proof.cursor.holds(LockClass.CONVERSATION, run.conversation_id)
        or not all(proof.cursor.holds(LockClass.AGENT_LIFECYCLE, a) for a in agent_ids)
    ):
        raise unsupported("inputs_unlocked")


def _execution_metadata(cfg: AgentConfig, display_names: dict[str, str]) -> AgentConfig:
    return dataclasses.replace(
        cfg,
        api_key=None,
        base_url=None,
        provider_api_keys=None,
        tools_config=[],
        model_fallback_chain=None,
        subagents_config=None,
        subagent_display_names=display_names or None,
        secret_values=set(),
    )


class PreparedRunResolver:
    """Callable ``prepare_inputs(db, run)`` for ``materialize_and_activate``."""

    def __init__(
        self,
        request: PreparedRunRequest,
        *,
        bindings: Callable[[], RuntimePreparationBindings] | None = None,
        memory: MemoryReads | None = None,
    ) -> None:
        self._request = request
        self._bindings = bindings or _runtime_preparation_bindings
        self._memory = memory or MemoryReads()
        self.execution_cfg: AgentConfig | None = None

    async def __call__(self, db: AsyncSession, run: ConversationRun) -> PreparationInputs:
        request = self._request
        if run.agent_id is None:
            raise agent_not_found()
        _require_locked(db, run, (run.agent_id,))
        await load_agent_tree_for_execution(
            db, AgentExecutionTarget(run.agent_id, request.context, run=True)
        )
        cfg = await request.resolve_config(db, run)
        bindings = self._bindings()
        trigger = request.is_trigger_mode
        require_lease_supported(
            cfg,
            builtin_types=bindings.deepagent_builtin_types,
            provider_middleware=bindings.get_provider_middleware(cfg.provider),
            memory_write_policy=await self._memory.write_policy(cfg, db, is_trigger_mode=trigger),
        )
        model = lease_model_spec(
            provider=cfg.provider,
            model_name=cfg.model_name,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model_params=cfg.model_params,
            context_window=cfg.context_window,
        )
        conversation = await chat_service.get_owned_conversation_with_agent(
            db, run.conversation_id, request.context
        )
        if conversation is None or conversation.agent is None:
            raise agent_not_found()
        agent = conversation.agent
        _require_locked(db, run, {agent.id, *(link.sub_agent_id for link in agent.sub_agent_links)})
        resolved = await resolve_prepared_children(
            db,
            agent,
            cfg,
            bindings,
            is_trigger_mode=trigger,
            write_policy=self._memory.write_policy,
        )
        cfg.secret_values |= resolved.secret_values
        memory_prompt, recalled = await self._memory.context(cfg, db)
        cfg.recalled_memories = recalled or None
        names = {child.runtime_name: child.display_name for child in resolved.children}
        self.execution_cfg = _execution_metadata(cfg, names)
        refs = [f"llm_credential:{cfg.llm_credential_id}"] if cfg.llm_credential_id else []
        return build_preparation_inputs(
            PreparedRunSources(
                cfg=cfg,
                model=model,
                messages_history=request.messages_history,
                data_dir=runtime_data_dir(),
                run_root=request.run_root_base / str(run.id),
                children=tuple(resolved.children),
                memory_prompt=memory_prompt,
                is_trigger_mode=trigger,
                secret_reference_ids=(*refs, *resolved.reference_ids),
                versions={
                    "agent_updated_at": agent.updated_at.isoformat() if agent.updated_at else "",
                    "runtime_policy_hash": cfg.runtime_policy.policy_hash,
                    **resolved.versions,
                },
            ),
            bindings=bindings,
        )
