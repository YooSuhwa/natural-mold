"""Conversation run worker compatibility facade (Task 10 I4 split).

Registry, transitions, delivery, and execution live in ``conversation_run_*``
modules; those resolve patched collaborators through this module at call time.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime import event_names
from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.protocol_redaction import REDACTED_SENSITIVE_FIELD
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_baseline import (
    baseline_completed_tool_call_source_identities,
    baseline_message_identities,
)
from app.agent_runtime.run_metrics_types import TerminalRunState
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.stream_error_messages import public_stream_error_message
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.worker_conversation_scope import (
    WorkerConversationTarget,
    load_worker_conversation,
)
from app.config import settings
from app.dependencies import CurrentUser
from app.error_codes import conversation_not_found
from app.exceptions import AppError
from app.marketplace.redaction import replace_secret_values
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service, thread_branch_service
from app.services import conversation_stream_service as stream_service
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_run_delivery import (
    _activate_latest_branch_leaf_if_needed,
    _audit_action_for_terminal_status,
    _backfill_turn_attachments,
    _parse_sse_chunk,
    _publish_error,
    _publish_message_end,
    _publish_stale,
    _publish_yielded_chunk_if_needed,
    _record_run_audit,
    _redact_run_error_message,
    _trace_status_for_run,
)
from app.services.conversation_run_execution import AgentStreamExecutor, _run_conversation
from app.services.conversation_run_interrupts import (
    has_interrupt_events,
    interrupt_id_from_events,
)
from app.services.conversation_run_metrics_service import persist_run_metrics_and_usage
from app.services.conversation_run_task_registry import (
    _CANCEL_POLL_SECONDS,
    CancelReason,
    RunTaskAlreadyRegisteredError,
    RunTaskRegistrationError,
    RunTaskRegistry,
)
from app.services.conversation_run_terminal_delivery import OwnerTerminalDelivery
from app.services.conversation_run_transitions import (
    _effective_terminal_status,
    _execution_target,
    _heartbeat_interval_seconds,
    _heartbeat_until_terminal,
    _persist_shutdown_intent,
    _transition,
    _transition_to_running,
)
from app.services.conversation_stream_service import StreamCtx

logger = logging.getLogger(__name__)

async_session = None


def _session_factory():
    return async_session or stream_service.async_session


_registry = RunTaskRegistry()


def get_run_task_registry() -> RunTaskRegistry:
    return _registry


def reset_run_task_registry_for_tests(registry: RunTaskRegistry | None = None) -> None:
    global _registry
    _registry = registry or RunTaskRegistry()


async def _prepare_run_metrics(
    *,
    conversation_id: uuid.UUID,
    cfg: AgentConfig,
    started_at: float,
) -> RunMetricsAccumulator:
    """Capture the exact pre-input assistant IDs used to exclude checkpoint replay."""
    try:
        async with _session_factory()() as session:
            conversation = await load_worker_conversation(
                session, WorkerConversationTarget(conversation_id, cfg.org_context)
            )
            if conversation is None:
                return RunMetricsAccumulator(
                    started_at=started_at,
                    complete_event_capture=False,
                    observe_protocol_events=False,
                )
            active_checkpoint_id = (
                getattr(cfg, "checkpoint_id", None)
                if getattr(cfg, "checkpoint_id", None) is not None
                else conversation.active_branch_checkpoint_id
                if conversation is not None
                else None
            )
        tree = await thread_branch_service.build_message_tree(
            get_checkpointer(),
            str(conversation_id),
            active_checkpoint_id=active_checkpoint_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "run metrics checkpoint baseline unavailable conversation_id=%s",
            conversation_id,
            exc_info=True,
        )
        return RunMetricsAccumulator(
            started_at=started_at,
            complete_event_capture=False,
            observe_protocol_events=False,
        )
    baseline_messages = tuple(node.message for node in tree.nodes)
    return RunMetricsAccumulator(
        started_at=started_at,
        baseline_message_identities=baseline_message_identities(baseline_messages),
        baseline_completed_tool_call_source_identities=(
            baseline_completed_tool_call_source_identities(baseline_messages)
        ),
    )


async def start_conversation_run(
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    cfg: AgentConfig,
    user: CurrentUser,
    input_payload: Any,
    moldy_source: str,
    executor_fn: AgentStreamExecutor,
    registry: RunTaskRegistry | None = None,
    attachment_ids: list[uuid.UUID] | None = None,
    config_transform: Callable[[AgentConfig], AgentConfig] | None = None,
) -> StreamCtx:
    target = _execution_target(run_id, conversation_id, cfg)
    if user.id != target.conversation.context.user_id:
        raise conversation_not_found()
    async with _session_factory()() as session:
        try:
            await load_agent_tree_for_execution(
                session,
                AgentExecutionTarget(
                    target.conversation.agent_id,
                    target.conversation.context,
                ),
            )
        except AppError as error:
            if error.code == "AGENT_ARCHIVED":
                raise
            raise conversation_not_found() from error
        if await lock_executable_run(session, target) is None:
            raise conversation_not_found()
        registry = registry or get_run_task_registry()
        ctx = stream_service.prepare_stream_context(conversation_id, run_id=str(run_id))
        task = asyncio.create_task(
            _run_conversation(
                run_id=run_id,
                conversation_id=conversation_id,
                cfg=cfg,
                user=user,
                input_payload=input_payload,
                moldy_source=moldy_source,
                executor_fn=executor_fn,
                ctx=ctx,
                registry=registry,
                attachment_ids=attachment_ids,
                config_transform=config_transform,
            ),
            name=f"conversation-run-{run_id}",
        )
        registry.start(run_id, task)
    return ctx


__all__ = [
    "RunTaskRegistry",
    "RunTaskRegistrationError",
    "RunTaskAlreadyRegisteredError",
    "get_run_task_registry",
    "reset_run_task_registry_for_tests",
    "start_conversation_run",
]
# Compatibility re-exports: names this module exposed before the I4 split.
__all__ += ["AgentStreamExecutor", "CancelReason", "_CANCEL_POLL_SECONDS", "StreamCtx"]
__all__ += ["BrokeredEvent", "EventBroker", "REDACTED_SENSITIVE_FIELD", "RunMetricsAccumulator"]
__all__ += ["baseline_completed_tool_call_source_identities", "baseline_message_identities"]
__all__ += ["TerminalRunState", "public_stream_error_message", "RunConversationTarget"]
__all__ += ["RunExecutionTarget", "WorkerConversationTarget", "load_worker_conversation"]
__all__ += ["settings", "replace_secret_values", "ConversationRun", "conversation_run_service"]
__all__ += ["thread_branch_service", "record_conversation_run_audit", "has_interrupt_events"]
__all__ += ["interrupt_id_from_events", "persist_run_metrics_and_usage", "OwnerTerminalDelivery"]
__all__ += ["select", "SQLAlchemyError", "event_names", "get_checkpointer"]
__all__ += ["_activate_latest_branch_leaf_if_needed", "_audit_action_for_terminal_status"]
__all__ += ["_backfill_turn_attachments", "_parse_sse_chunk", "_publish_error", "_publish_stale"]
__all__ += ["_publish_message_end", "_publish_yielded_chunk_if_needed", "_record_run_audit"]
__all__ += ["_redact_run_error_message", "_trace_status_for_run", "_run_conversation"]
__all__ += ["_effective_terminal_status", "_heartbeat_interval_seconds", "_transition"]
__all__ += ["_heartbeat_until_terminal", "_persist_shutdown_intent", "_execution_target"]
__all__ += ["_transition_to_running"]
