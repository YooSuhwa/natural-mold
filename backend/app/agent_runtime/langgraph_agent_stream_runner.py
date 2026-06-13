from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import nullcontext
from typing import Any

from langgraph.types import Command

from app.agent_runtime.agent_stream_runner import _hook_ctx_for_agent, _hook_result_from_usage
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.runtime_component_builder import _prepare_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.hooks import hooks
from app.observability.langfuse import LangfuseTraceRecord, build_langfuse_run_context


async def _run_langgraph_agent_stream(
    cfg: AgentConfig,
    *,
    messages_history: list[dict[str, str]],
    stream_input: Any,
    trace_sink: list[dict[str, Any]] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    moldy_source: str = "chat",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
) -> AsyncGenerator[str, None]:
    agent, lc_messages, config = await _prepare_agent(
        cfg,
        messages_history=messages_history,
    )
    actual_input = lc_messages if stream_input is _USE_PREPPED_LC_MESSAGES else stream_input
    if actual_input == []:
        actual_input = None

    langfuse_ctx = build_langfuse_run_context(
        cfg,
        run_id=run_id,
        source=moldy_source,
    )
    config = langfuse_ctx.configure_config(config)
    if langfuse_sink is not None and langfuse_ctx.trace is not None:
        langfuse_sink.append(langfuse_ctx.trace)

    ctx = _hook_ctx_for_agent(cfg)
    if ctx is not None:
        await hooks.run_pre(ctx)
    started = time.monotonic()
    stream_errors = error_sink if error_sink is not None else []

    activate = getattr(langfuse_ctx, "activate", None)
    activation: Any = (
        activate(input_payload=actual_input, output_payload=None)
        if callable(activate)
        else nullcontext()
    )
    try:
        with activation:
            async for chunk in stream_agent_response_langgraph(
                agent,
                actual_input,
                config,
                trace_sink=trace_sink,
                error_sink=stream_errors,
                broker=broker,
                persist_callback=persist_callback,
                run_id=run_id,
            ):
                yield chunk
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if ctx is not None:
            await hooks.run_failure(ctx, exc)
        raise
    finally:
        langfuse_ctx.flush()

    if stream_errors:
        if ctx is not None:
            await hooks.run_failure(ctx, stream_errors[0].error)
        return
    if ctx is not None:
        await hooks.run_post(
            ctx,
            _hook_result_from_usage(int((time.monotonic() - started) * 1000), {}),
        )


_USE_PREPPED_LC_MESSAGES: Any = object()


async def execute_agent_stream_langgraph(
    cfg: AgentConfig,
    messages_history: list[dict[str, str]] | dict[str, Any],
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    moldy_source: str = "chat",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
) -> AsyncGenerator[str, None]:
    if isinstance(messages_history, dict):
        async for chunk in _run_langgraph_agent_stream(
            cfg,
            messages_history=[],
            stream_input=messages_history,
            trace_sink=trace_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id=run_id,
            moldy_source=moldy_source,
            langfuse_sink=langfuse_sink,
        ):
            yield chunk
        return

    async for chunk in _run_langgraph_agent_stream(
        cfg,
        messages_history=messages_history,
        stream_input=_USE_PREPPED_LC_MESSAGES,
        trace_sink=trace_sink,
        error_sink=error_sink,
        broker=broker,
        persist_callback=persist_callback,
        run_id=run_id,
        moldy_source=moldy_source,
        langfuse_sink=langfuse_sink,
    ):
        yield chunk


async def resume_agent_stream_langgraph(
    cfg: AgentConfig,
    resume_value: Any,
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    moldy_source: str = "resume",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
) -> AsyncGenerator[str, None]:
    async for chunk in _run_langgraph_agent_stream(
        cfg,
        messages_history=[],
        stream_input=Command(resume=resume_value),
        trace_sink=trace_sink,
        error_sink=error_sink,
        broker=broker,
        persist_callback=persist_callback,
        run_id=run_id,
        moldy_source=moldy_source,
        langfuse_sink=langfuse_sink,
    ):
        yield chunk
