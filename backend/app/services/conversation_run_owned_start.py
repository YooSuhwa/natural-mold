"""Prepare an ordinary conversation worker or adopt an already claimed continuation."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

from app.agent_runtime.langgraph_agent_stream_runner import execute_agent_stream_langgraph
from app.agent_runtime.runtime.direct_stream_retry import (
    configured_attempts,
    direct_stream_retry_for,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.worker_conversation_scope import WorkerConversationTarget
from app.chat_recovery.identity import (
    RecoveryPolicyChanged,
    recovery_runtime_digest,
)
from app.config import settings
from app.dependencies import CurrentUser
from app.services import prepared_conversation_start as prepared_start
from app.services.conversation_run_fence import RunFence, RunOwnershipLost, lock_run_fence
from app.services.conversation_run_task_registry import _tenant_session, worker_attr
from app.services.conversation_run_transitions import _execution_target, _transition_to_running


def _ordinary_user_input(payload: Any) -> bool:
    """Only a new single text message. Compound resources and branch edits need new proofs."""
    if not isinstance(payload, dict) or set(payload) != {"messages"}:
        return False
    messages = payload["messages"]
    if not isinstance(messages, list) or len(messages) != 1:
        return False
    message = messages[0]
    if not isinstance(message, dict) or (message.get("role") or message.get("type")) not in {
        "user",
        "human",
    }:
        return False
    if message.keys() - {"role", "type", "id", "content", "additional_kwargs", "response_metadata"}:
        return False
    if message.get("additional_kwargs") or message.get("response_metadata"):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return "<resource-context-json>" not in content
    return (
        bool(content)
        and isinstance(content, list)
        and all(
            isinstance(part, dict)
            and set(part) == {"type", "text"}
            and part["type"] == "text"
            and isinstance(part["text"], str)
            and "<resource-context-json>" not in part["text"]
            for part in content
        )
    )


async def prepare_owned_start(
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    cfg: AgentConfig,
    user: CurrentUser,
    input_payload: Any,
    moldy_source: str,
    executor_fn: Any,
    attachment_ids: list[uuid.UUID] | None,
    config_transform: Callable[[AgentConfig], AgentConfig] | None,
    worker_instance_id: str,
    started_at: float,
    recovery_fence: RunFence | None,
) -> tuple[prepared_start.PreparedRunStart, AgentConfig, Any]:
    target = _execution_target(run_id, conversation_id, cfg)
    if recovery_fence is not None:
        # The recovery orchestrator resolves the actor/config afresh and claims the row.
        # Touch neither pending input nor attachments/guardrail metadata on adoption.
        if (
            recovery_fence.run_id != run_id
            or recovery_fence.conversation_id != conversation_id
            or recovery_fence.worker_instance_id != worker_instance_id
        ):
            raise RunOwnershipLost("Mismatched continuation")
        async with _tenant_session(cfg.org_context) as session:
            run = await lock_run_fence(session, recovery_fence)
            if (
                not run.recovery_runtime_digest
                or run.recovery_runtime_digest != recovery_runtime_digest(cfg)
            ):
                raise RecoveryPolicyChanged
            # Tenant context manager rolls the read transaction back on exit, expiring mapped state.
            session.expunge(run)
        return prepared_start.PreparedRunStart(run, True), cfg, input_payload

    conversation = WorkerConversationTarget(conversation_id, cfg.org_context)
    input_payload, attachment_inputs = await worker_attr("_prepare_turn_attachment_inputs")(
        run_id,
        conversation,
        cfg.model_id,
        attachment_ids,
        input_payload,
        tag_input=moldy_source != "resume",
    )
    if attachment_inputs is not None:
        cfg = dataclasses.replace(cfg, attachment_inputs=attachment_inputs)
    pinned = await worker_attr("_prepare_turn_guardrails")(
        run_id,
        conversation,
        cfg.agent_id,
        cfg.runtime_profile,
        resume=moldy_source == "resume",
    )
    cfg = dataclasses.replace(
        cfg, guardrail_rules=pinned.rules, guardrail_resolved=True, tool_risk=pinned.tool_risk
    )
    transition = _transition_to_running
    if (
        settings.chat_run_recovery_enabled
        and moldy_source in {"chat", "start"}
        and cfg.run_path == "chat"
        and cfg.runtime_profile == "standard"
        and not attachment_ids
        and cfg.attachment_inputs is None
        and config_transform is None
        and executor_fn is execute_agent_stream_langgraph
        and _ordinary_user_input(input_payload)
    ):
        digest = recovery_runtime_digest(cfg)
        if digest is not None:
            attempt_limit = (
                configured_attempts(cfg.middleware_configs)
                if direct_stream_retry_for(cfg)
                else None
            )
            transition = partial(
                _transition_to_running,
                recovery_digest=digest,
                recovery_model_attempt_limit=attempt_limit,
            )

    start = await prepared_start.start_owned_run(
        target,
        prepared_start.OwnedRunInputs(cfg, user, input_payload, executor_fn, config_transform),
        worker_instance_id=worker_instance_id,
        started_at=started_at,
        transition=transition,
    )
    return start, start.cfg or cfg, input_payload
