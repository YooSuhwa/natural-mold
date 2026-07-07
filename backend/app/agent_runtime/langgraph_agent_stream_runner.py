from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import nullcontext
from typing import Any

from langgraph.types import Command

from app.agent_runtime.agent_stream_runner import _hook_ctx_for_agent, _hook_result_from_usage
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets
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
    msg_id_sink: list[str] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    artifact_recorder: Any | None = None,
    moldy_source: str = "chat",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
) -> AsyncGenerator[str, None]:
    # ADR-021 C1 — install the run-scoped secret set BEFORE ``_prepare_agent``
    # (mirrors agent_stream_runner.py:119/144) so the lazy skill-credential
    # union in ``_prepare_runtime_components`` (incl. subagents) mutates the
    # same object, and so the redaction ContextVar is live for the whole
    # streaming generator. ``emit`` (langgraph_streaming) and the
    # ``persist_callback`` both run in this same async task — directly, not via
    # a spawned task — so the ContextVar survives across yields (ADR §2 / C5).
    # ``reset`` happens in ``finally`` so the set never leaks into the next run
    # on this task.
    secret_token = set_run_secrets(cfg.secret_values)
    try:
        async for chunk in _stream_langgraph_with_secrets(
            cfg,
            messages_history=messages_history,
            stream_input=stream_input,
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id=run_id,
            artifact_recorder=artifact_recorder,
            moldy_source=moldy_source,
            langfuse_sink=langfuse_sink,
        ):
            yield chunk
    finally:
        reset_run_secrets(secret_token)


async def _stream_langgraph_with_secrets(
    cfg: AgentConfig,
    *,
    messages_history: list[dict[str, str]],
    stream_input: Any,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    artifact_recorder: Any | None = None,
    moldy_source: str = "chat",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
) -> AsyncGenerator[str, None]:
    """Inner body — runs with the run-scoped secret ContextVar already set.

    Split out of ``_run_langgraph_agent_stream`` so the set/reset can wrap
    prepare + the full stream without nesting the existing hook / langfuse
    try/finally inside an extra indentation level (mirrors
    ``agent_stream_runner._stream_with_secrets``).
    """

    agent, lc_messages, config = await _prepare_agent(
        cfg,
        messages_history=messages_history,
    )
    actual_input = lc_messages if stream_input is _USE_PREPPED_LC_MESSAGES else stream_input
    if actual_input == []:
        actual_input = None

    _append_run_message_id(msg_id_sink, run_id)

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
    usage_sink: dict[str, Any] = {}
    stream_errors = error_sink if error_sink is not None else []
    protocol_trace = trace_sink if trace_sink is not None else []

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
                trace_sink=protocol_trace,
                cost_per_input_token=cfg.cost_per_input_token,
                cost_per_output_token=cfg.cost_per_output_token,
                usage_sink=usage_sink,
                error_sink=stream_errors,
                broker=broker,
                persist_callback=persist_callback,
                run_id=run_id,
                artifact_recorder=artifact_recorder,
                subagent_display_names=cfg.subagent_display_names,
                # W2-3 — _prepare_agent(위)가 회상된 memory brief를 cfg에
                # 채워두면 stream head에서 moldy.memory_recalled로 방출된다.
                recalled_memories=cfg.recalled_memories,
                # AD-5 — resolve_agent_context가 드래프트 요약을 cfg에 채우면
                # stream head에서 moldy.skill_draft로 방출된다 (동일 계약).
                skill_draft_brief=cfg.skill_draft_brief,
            ):
                yield chunk
    except asyncio.CancelledError:
        if ctx is not None and usage_sink:
            await hooks.run_post(
                ctx,
                _hook_result_from_usage(
                    int((time.monotonic() - started) * 1000),
                    usage_sink,
                ),
            )
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
            _hook_result_from_usage(int((time.monotonic() - started) * 1000), usage_sink),
        )


_USE_PREPPED_LC_MESSAGES: Any = object()


def _append_run_message_id(msg_id_sink: list[str] | None, run_id: str | None) -> None:
    if msg_id_sink is None or not run_id or run_id in msg_id_sink:
        return
    msg_id_sink.append(run_id)


async def execute_agent_stream_langgraph(
    cfg: AgentConfig,
    messages_history: list[dict[str, str]] | dict[str, Any],
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
    ):
        yield chunk
