"""Prepared-lease variant of the LangGraph v3 stream runner (parent Task 10, I19).

Mirrors ``agent_stream_execution._run_prepared_agent_stream`` for the v3
protocol body: ``cfg`` is Langfuse/cost/display metadata only (hooks
skipped) and ``_prepare_agent`` is never called; ``prepared_dispatch`` claims
the exact lease, installs its secret set, runs under ``post_marker_execution``
and terminalizes it however the stream ends.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from app.agent_runtime.langgraph_stream_execution import _stream_langgraph_body
from app.agent_runtime.prepared_run_dispatch import (
    USE_LEASE_MESSAGES,
    PreparedRunHandle,
    lease_graph_input,
    lease_run_config,
    prepared_dispatch,
)
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.observability.langfuse import LangfuseTraceRecord


async def _run_prepared_langgraph_stream(
    cfg: AgentConfig,
    handle: PreparedRunHandle,
    *,
    stream_input: Any = USE_LEASE_MESSAGES,
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
    usage_sink: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream the exact activated lease graph through the v3 protocol body."""

    async with prepared_dispatch(handle) as lease:
        async for chunk in _stream_langgraph_body(
            cfg,
            agent=lease.runnable,
            actual_input=lease_graph_input(lease, stream_input),
            config=lease_run_config(lease),
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id=run_id or str(lease.binding.run_id),
            artifact_recorder=artifact_recorder,
            moldy_source=moldy_source,
            langfuse_sink=langfuse_sink,
            run_metrics=run_metrics,
            hooks_enabled=False,
            usage_sink=usage_sink,
        ):
            yield chunk
