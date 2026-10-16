"""Shared prepare + hook + Langfuse stream body for the legacy SSE runner.

Collaborators patched on ``agent_stream_runner`` are resolved at call time.
``_run_prepared_agent_stream`` is the opt-in prepared-lease variant (Task 10
I20): the frozen lease graph replaces ``_prepare_agent``.
"""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import nullcontext
from typing import Any

from app.agent_runtime.agent_stream_facade_bindings import stream_attr
from app.agent_runtime.message_utils import RuntimeMessage
from app.agent_runtime.prepared_run_dispatch import (
    USE_LEASE_MESSAGES,
    PreparedRunHandle,
    lease_graph_input,
    lease_run_config,
    prepared_dispatch,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.observability.langfuse import LangfuseTraceRecord


async def _run_agent_stream(
    cfg: AgentConfig,
    *,
    messages_history: Sequence[RuntimeMessage],
    stream_input: Any,
    hook_metadata_extra: dict[str, Any] | None = None,
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
    """공용 stream runner — execute/resume의 prep + hook + 예외 처리 통합 (P0-B).

    - ``messages_history``는 ``_prepare_agent``에 전달 (lc_messages 변환에만 사용).
    - ``stream_input``은 ``stream_agent_response``에 전달할 입력. ``None`` 이면
      execute_agent_stream가 변환한 lc_messages를 그대로 쓴다 (즉 execute는
      stream_input=None 또는 명시 list, resume은 ``Command(resume=...)``).
    - ``hook_metadata_extra``: HookContext.metadata에 추가로 머지(resume용).
    - ``broker`` / ``persist_callback`` / ``run_id`` (W3-out M2): SSE dual-write
      + partial flush 파이프라인. router가 EventBroker, fresh-session-bound
      append_events 콜백, run_id(=assistant_msg_id) 를 주입.
    """

    # ADR-021 — install the run-scoped secret set BEFORE prepare so the lazy
    # skill-credential union in ``_prepare_runtime_components`` (incl. subagents)
    # mutates the same object, and so the redaction ContextVar is live for the
    # whole streaming generator. ``reset`` happens in the outer ``finally`` to
    # avoid leaking the set into the next run on this task.
    effective_run_id = run_id or str(_uuid.uuid4())
    secret_token = stream_attr("set_run_secrets")(cfg.secret_values)
    try:
        agent, lc_messages, config = await stream_attr("_prepare_agent")(
            cfg,
            messages_history=messages_history,
            is_trigger_mode=False,
            run_id=effective_run_id,
        )
        async for chunk in stream_attr("_stream_with_secrets")(
            cfg,
            agent=agent,
            lc_messages=lc_messages,
            config=config,
            stream_input=stream_input,
            hook_metadata_extra=hook_metadata_extra,
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id=effective_run_id,
            artifact_recorder=artifact_recorder,
            moldy_source=moldy_source,
            langfuse_sink=langfuse_sink,
        ):
            yield chunk
    finally:
        stream_attr("reset_run_secrets")(secret_token)


async def _stream_with_secrets(
    cfg: AgentConfig,
    *,
    agent: Any,
    lc_messages: list[Any],
    config: dict[str, Any],
    stream_input: Any,
    hook_metadata_extra: dict[str, Any] | None,
    trace_sink: list[dict[str, Any]] | None,
    msg_id_sink: list[str] | None,
    error_sink: list[StreamErrorRecord] | None,
    broker: Any | None,
    persist_callback: Any | None,
    run_id: str | None,
    artifact_recorder: Any | None,
    moldy_source: str,
    langfuse_sink: list[LangfuseTraceRecord] | None,
    hooks_enabled: bool = True,
) -> AsyncGenerator[str, None]:
    """Inner streaming body — runs with the run-scoped secret ContextVar set.

    Split out of ``_run_agent_stream`` so the secret ContextVar set/reset can
    wrap prepare + the full stream without nesting the existing hook /
    langfuse try/finally inside an extra indentation level.
    """

    # stream_input이 ``_USE_PREPPED_LC_MESSAGES`` sentinel이면 변환된 lc_messages를
    # 그대로 입력으로 사용 (execute path). 빈 리스트는 None으로 폴백 — LangGraph
    # time-travel resume 모드.
    actual_input = stream_input
    if actual_input is _USE_PREPPED_LC_MESSAGES:
        actual_input = lc_messages if lc_messages else None

    langfuse_ctx = stream_attr("build_langfuse_run_context")(
        cfg,
        run_id=run_id,
        source=moldy_source,
    )
    config = langfuse_ctx.configure_config(config)
    if langfuse_sink is not None and langfuse_ctx.trace is not None:
        langfuse_sink.append(langfuse_ctx.trace)

    # Prepared leases skip the DB-writing hooks (outside the I14 write capability).
    ctx = stream_attr("_hook_ctx_for_agent")(cfg) if hooks_enabled else None
    if ctx is not None:
        if hook_metadata_extra:
            ctx.metadata.update(hook_metadata_extra)
        await stream_attr("hooks").run_pre(ctx)
    started = time.monotonic()
    usage_sink: dict[str, Any] = {}
    stream_errors = error_sink if error_sink is not None else []

    activate = getattr(langfuse_ctx, "activate", None)
    activation: Any = (
        activate(input_payload=actual_input, output_payload=None)
        if callable(activate)
        else nullcontext()
    )
    try:
        with activation:
            async for chunk in stream_attr("stream_agent_response")(
                agent,
                actual_input,
                config,
                cost_per_input_token=cfg.cost_per_input_token,
                cost_per_output_token=cfg.cost_per_output_token,
                usage_sink=usage_sink,
                trace_sink=trace_sink,
                msg_id_sink=msg_id_sink,
                error_sink=stream_errors,
                broker=broker,
                persist_callback=persist_callback,
                run_id=run_id,
                subagent_display_names=cfg.subagent_display_names,
                artifact_recorder=artifact_recorder,
            ):
                yield chunk
    except asyncio.CancelledError:
        if ctx is not None and usage_sink:
            await stream_attr("hooks").run_post(
                ctx,
                stream_attr("_hook_result_from_usage")(
                    int((time.monotonic() - started) * 1000),
                    usage_sink,
                ),
            )
        raise
    except Exception as exc:
        if ctx is not None:
            await stream_attr("hooks").run_failure(ctx, exc)
        raise
    finally:
        langfuse_ctx.flush()
    if stream_errors:
        if ctx is not None:
            await stream_attr("hooks").run_failure(ctx, stream_errors[0].error)
        return
    if ctx is not None:
        await stream_attr("hooks").run_post(
            ctx,
            stream_attr("_hook_result_from_usage")(
                int((time.monotonic() - started) * 1000), usage_sink
            ),
        )


# Sentinel that tells ``_run_agent_stream`` to feed its prepped lc_messages
# straight into ``stream_agent_response`` (execute path). Resume path passes a
# concrete ``Command(resume=...)`` instead.
_USE_PREPPED_LC_MESSAGES: Any = object()


async def _run_prepared_agent_stream(
    cfg: AgentConfig,
    handle: PreparedRunHandle,
    *,
    stream_input: Any = USE_LEASE_MESSAGES,
    hook_metadata_extra: dict[str, Any] | None = None,
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
    """Stream the exact activated lease graph.

    ``cfg`` is Langfuse/cost metadata only (hooks skipped). Never calls
    ``_prepare_agent``: ``prepared_dispatch`` claims the exact
    lease, installs its secret set, runs under ``post_marker_execution`` and
    terminalizes the lease (children first) however the stream ends.
    """

    async with prepared_dispatch(handle) as lease:
        async for chunk in stream_attr("_stream_with_secrets")(
            cfg,
            agent=lease.runnable,
            lc_messages=[],
            config=lease_run_config(lease),
            stream_input=lease_graph_input(lease, stream_input),
            hook_metadata_extra=hook_metadata_extra,
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id=run_id or str(lease.binding.run_id),
            artifact_recorder=artifact_recorder,
            moldy_source=moldy_source,
            langfuse_sink=langfuse_sink,
            hooks_enabled=False,
        ):
            yield chunk
