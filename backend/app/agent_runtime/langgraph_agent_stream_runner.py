"""LangGraph v3 stream runner — compatibility facade.

The prepare + hook + Langfuse body lives in ``langgraph_stream_execution``;
tests patch this module's attributes (``_prepare_agent``, ``hooks``,
``stream_agent_response_langgraph`` ...) after import and the extracted body
resolves them here at call time via ``agent_stream_facade_bindings``.
"""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import nullcontext
from typing import Any

from langgraph.types import Command

from app.agent_runtime.agent_stream_runner import _hook_ctx_for_agent, _hook_result_from_usage
from app.agent_runtime.langgraph_prepared_stream import _run_prepared_langgraph_stream
from app.agent_runtime.langgraph_stream_execution import (
    _USE_PREPPED_LC_MESSAGES,
    _append_run_message_id,
    _run_langgraph_agent_stream,
    _stream_langgraph_with_secrets,
)
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.message_utils import RuntimeMessage
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_callback import configure_run_metrics_callback
from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets
from app.agent_runtime.runtime_component_builder import _prepare_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.hooks import hooks
from app.observability.langfuse import LangfuseTraceRecord, build_langfuse_run_context

# Legacy import names kept on the facade (patching stdlib names here does not reach moved bodies).
__all__ = ["_append_run_message_id", "_hook_ctx_for_agent", "_hook_result_from_usage"]
__all__ += ["_prepare_agent", "_stream_langgraph_with_secrets", "asyncio", "time", "_uuid"]
__all__ += ["build_langfuse_run_context", "configure_run_metrics_callback", "hooks"]
__all__ += ["nullcontext", "reset_run_secrets", "set_run_secrets"]
__all__ += ["stream_agent_response_langgraph"]
# Opt-in prepared-lease (Task 10 I19) entry point.
__all__ += ["_run_prepared_langgraph_stream"]


async def execute_agent_stream_langgraph(
    cfg: AgentConfig,
    messages_history: Sequence[RuntimeMessage] | dict[str, Any],
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    artifact_recorder: Any | None = None,
    moldy_source: str = "chat",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
    run_metrics: RunMetricsAccumulator | None = None,
) -> AsyncGenerator[str, None]:
    if isinstance(messages_history, dict):
        async for chunk in _run_langgraph_agent_stream(
            cfg,
            messages_history=[],
            stream_input=messages_history,
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id=run_id,
            artifact_recorder=artifact_recorder,
            moldy_source=moldy_source,
            langfuse_sink=langfuse_sink,
            run_metrics=run_metrics,
        ):
            yield chunk
        return

    async for chunk in _run_langgraph_agent_stream(
        cfg,
        messages_history=messages_history,
        stream_input=_USE_PREPPED_LC_MESSAGES,
        trace_sink=trace_sink,
        msg_id_sink=msg_id_sink,
        error_sink=error_sink,
        broker=broker,
        persist_callback=persist_callback,
        run_id=run_id,
        artifact_recorder=artifact_recorder,
        moldy_source=moldy_source,
        langfuse_sink=langfuse_sink,
        run_metrics=run_metrics,
    ):
        yield chunk


async def resume_agent_stream_langgraph(
    cfg: AgentConfig,
    resume_value: Any,
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    artifact_recorder: Any | None = None,
    moldy_source: str = "resume",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
    run_metrics: RunMetricsAccumulator | None = None,
) -> AsyncGenerator[str, None]:
    async for chunk in _run_langgraph_agent_stream(
        cfg,
        messages_history=[],
        stream_input=Command(resume=resume_value),
        trace_sink=trace_sink,
        msg_id_sink=msg_id_sink,
        error_sink=error_sink,
        broker=broker,
        persist_callback=persist_callback,
        run_id=run_id,
        artifact_recorder=artifact_recorder,
        moldy_source=moldy_source,
        langfuse_sink=langfuse_sink,
        run_metrics=run_metrics,
    ):
        yield chunk
