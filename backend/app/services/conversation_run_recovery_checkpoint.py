"""Authorize the exact checkpoint, visible event prefix and tool receipts before takeover."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
from app.agent_runtime.langgraph_stream_execution import _run_langgraph_agent_stream
from app.agent_runtime.message_utils import flatten_base_messages
from app.agent_runtime.recovery_stream import (
    RecoveryOutputStarted,
    RecoveryStreamCursor,
    continue_protocol_delivery,
)
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_recovery import restore_recovery_metrics
from app.agent_runtime.run_recovery_checkpoint import (
    RECOVER_GRAPH_HEAD,
    RecoveryCheckpointBlocked,
    checked_head,
    turn_tools,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.chat_recovery.receipts import blocking_receipt_code
from app.database import tenant_session
from app.models.conversation_run import ConversationRun
from app.services import thread_branch_service
from app.services.conversation_recovery_stream import load_recovery_stream_cursor
from app.services.conversation_run_fence import current_run_fence, lock_run_fence

__all__ = ["RecoveryCheckpointBlocked", "continue_recovery_stream", "resolve_recovery_checkpoint"]


async def resolve_recovery_checkpoint(
    db: AsyncSession, run: ConversationRun, cfg: AgentConfig
) -> str:
    """Caller holds the scoped run row lock; no graph or tool is executed here."""
    checkpoint_id, _cursor = await _recovery_head_and_cursor(db, run, cfg)
    return checkpoint_id


async def _recovery_head_and_cursor(
    db: AsyncSession,
    run: ConversationRun,
    cfg: AgentConfig,
    *,
    metrics: RunMetricsAccumulator | None = None,
) -> tuple[str, RecoveryStreamCursor]:
    scope = cfg.org_context
    if (
        str(run.conversation_id) != cfg.thread_id
        or scope.tenant_id != run.tenant_id
        or scope.org_id != run.org_id
        or scope.user_id != run.user_id
        or str(run.agent_id) != cfg.agent_id
        or cfg.runtime_profile != "standard"
    ):
        raise RecoveryCheckpointBlocked()
    saver = await conversation_checkpointer(cfg.thread_id, scope, db=db)
    saved = await saver.aget_tuple(
        {"configurable": {"thread_id": cfg.thread_id, "checkpoint_ns": ""}}
    )
    if saved is None:
        raise RecoveryCheckpointBlocked()
    checkpoint_id = checked_head(saved, cfg, run.id)
    # The reducer stores a full message list or a checkpoint plus its deltas.
    # Materialize the exact head before comparing it to visible tool events.
    values = saved.checkpoint["channel_values"]
    if "messages" in values:
        messages = thread_branch_service._normalize_checkpoint_messages(  # noqa: SLF001
            values["messages"], source="recovery checkpoint"
        )
    else:
        messages = await thread_branch_service.materialize_messages_at_checkpoint(
            saver, cfg.thread_id, checkpoint_id
        )
    # Only committed tool results can prove a non-replayable effect. A pending
    # model write may explain an already visible tool request, but cannot prove
    # an external action. The compiled live state confirms the request again.
    committed = turn_tools(messages, run.id)
    for _, channel, value in saved.pending_writes or ():
        if channel == "messages":
            messages.extend(flatten_base_messages(value, source="recovery pending writes"))
    tools = turn_tools(messages, run.id)
    if code := await blocking_receipt_code(db, run.id, completed_call_ids=committed.completed):
        raise RecoveryCheckpointBlocked(code)
    prefix: list[dict[str, Any]] = []
    try:
        cursor = await load_recovery_stream_cursor(
            db,
            conversation_id=run.conversation_id,
            run_id=run.id,
            observe_prefix=prefix.extend if metrics is not None else None,
        )
    except RecoveryOutputStarted as error:
        raise RecoveryCheckpointBlocked("recovery_output_started") from error
    if not cursor.visible_tool_call_ids.issubset(tools.requested):
        raise RecoveryCheckpointBlocked("recovery_output_started")
    if metrics is not None:
        restore_recovery_metrics(
            metrics, run_id=str(run.id), thread_id=cfg.thread_id, messages=messages, events=prefix
        )
    return checkpoint_id, cursor


async def continue_recovery_stream(
    cfg: AgentConfig,
    input_payload: Any,
    *,
    run_id: str,
    moldy_source: str,
    **stream_kwargs: Any,
) -> AsyncGenerator[str, None]:
    fence = current_run_fence()
    if (
        fence is None
        or run_id != str(fence.run_id)
        or cfg.thread_id != str(fence.conversation_id)
        or input_payload not in (None, [])
        or moldy_source != "recovery"
    ):
        raise RecoveryCheckpointBlocked()
    async with tenant_session(cfg.org_context) as db:
        run = await lock_run_fence(db, fence)
        checkpoint_id, cursor = await _recovery_head_and_cursor(
            db, run, cfg, metrics=stream_kwargs.get("run_metrics")
        )
        if checkpoint_id != cfg.checkpoint_id:
            raise RecoveryCheckpointBlocked()
    with continue_protocol_delivery(cursor):
        async for chunk in _run_langgraph_agent_stream(
            cfg,
            messages_history=[],
            stream_input=RECOVER_GRAPH_HEAD,
            run_id=run_id,
            moldy_source=moldy_source,
            **stream_kwargs,
        ):
            yield chunk
