from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from collections.abc import AsyncGenerator
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets
from app.agent_runtime.runtime_component_builder import _prepare_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord, stream_agent_response
from app.hooks import HookContext, HookResult, hooks
from app.observability.langfuse import LangfuseTraceRecord, build_langfuse_run_context


def _hook_ctx_for_agent(cfg: AgentConfig) -> HookContext | None:
    """Build a ``HookContext`` for an ``agent_invoke`` call.

    Returns ``None`` when the caller didn't propagate a ``user_id`` (legacy
    tests that build ``AgentConfig`` directly). Hook dispatch is a no-op in
    that case so the runtime stays backward compatible.

    Correlation IDs (``agent_id`` / ``model_id`` / ``llm_credential_id``)
    are best-effort: a malformed UUID drops the field instead of crashing
    the request — these are trace metadata, not access-control gates.
    The user_id check above is the security boundary.
    """

    if not cfg.user_id:
        return None
    try:
        user_uuid = _uuid.UUID(str(cfg.user_id))
    except (TypeError, ValueError):
        return None

    def _opt(value: str | None) -> _uuid.UUID | None:
        if not value:
            return None
        try:
            return _uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    metadata: dict[str, Any] = {
        "provider": cfg.provider,
        "model_name": cfg.model_name,
        "thread_id": cfg.thread_id,
        "identity_mode": cfg.identity_mode,
        "credential_subject_user_id": cfg.credential_subject_user_id,
        "agent_runtime_name": cfg.agent_runtime_name,
    }
    return HookContext(
        request_id=str(_uuid.uuid4()),
        kind="agent_invoke",
        user_id=user_uuid,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        agent_id=_opt(cfg.agent_id),
        model_id=_opt(cfg.model_id),
        credential_id=_opt(cfg.llm_credential_id),
        metadata=metadata,
    )


def _hook_result_from_usage(duration_ms: int, usage_sink: dict[str, Any]) -> HookResult:
    """Build a :class:`HookResult` from streaming-captured usage.

    Streaming surfaces ``prompt_tokens`` / ``completion_tokens`` /
    ``estimated_cost`` keys; the hook framework maps them to its own
    ``tokens_in`` / ``tokens_out`` / ``cost_usd`` field names.
    """

    prompt = usage_sink.get("prompt_tokens")
    completion = usage_sink.get("completion_tokens")
    cost = usage_sink.get("estimated_cost")
    return HookResult(
        duration_ms=duration_ms,
        tokens_in=int(prompt) if prompt is not None else None,
        tokens_out=int(completion) if completion is not None else None,
        cost_usd=float(cost) if cost is not None else None,
    )


async def _run_agent_stream(
    cfg: AgentConfig,
    *,
    messages_history: list[dict[str, str]],
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
    secret_token = set_run_secrets(cfg.secret_values)
    try:
        agent, lc_messages, config = await _prepare_agent(
            cfg,
            messages_history=messages_history,
        )
        async for chunk in _stream_with_secrets(
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
            run_id=run_id,
            artifact_recorder=artifact_recorder,
            moldy_source=moldy_source,
            langfuse_sink=langfuse_sink,
        ):
            yield chunk
    finally:
        reset_run_secrets(secret_token)


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
        if hook_metadata_extra:
            ctx.metadata.update(hook_metadata_extra)
        await hooks.run_pre(ctx)
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
            async for chunk in stream_agent_response(
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


# Sentinel that tells ``_run_agent_stream`` to feed its prepped lc_messages
# straight into ``stream_agent_response`` (execute path). Resume path passes a
# concrete ``Command(resume=...)`` instead.
_USE_PREPPED_LC_MESSAGES: Any = object()


async def execute_agent_stream(
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
    """스트리밍 실행 (채팅용).

    빈 ``messages_history``는 LangGraph time-travel resume 모드 — 새 입력
    없이 ``cfg.checkpoint_id`` 시점 state에서 그래프를 다시 돌린다.
    Regenerate가 부모 user 메시지를 중복 주입하지 않고 새 assistant sibling
    만 만들어내는 데 사용한다.

    ``trace_sink`` (W5): 호출자가 list를 넘기면 emit된 SSE 이벤트 dict가 차곡
    차곡 누적된다. 스트림 종료 시점에 caller가 ``trace_storage.record_turn``
    으로 영속화.

    ``broker`` / ``persist_callback`` / ``run_id`` (W3-out M2): GET resume
    파이프라인. router가 주입하면 dual-write + partial flush 활성화.
    """

    # dict input — fork-edit가 Overwrite({"messages": [...]}) 같은 형태로
    # state 채널을 직접 덮어쓸 때 사용. 이 경우 _prepare_agent는 lc_messages
    # 변환을 건너뛰고(빈 리스트), stream_input으로 dict를 그대로 흘려보낸다.
    if isinstance(messages_history, dict):
        async for chunk in _run_agent_stream(
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

    async for chunk in _run_agent_stream(
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


async def resume_agent_stream(
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
    """인터럽트 재개 스트리밍 (HiTL resume)."""
    from langgraph.types import Command

    async for chunk in _run_agent_stream(
        cfg,
        messages_history=[],
        stream_input=Command(resume=resume_value),
        hook_metadata_extra={"resume": True},
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


async def execute_agent_invoke(
    cfg: AgentConfig,
    messages_history: list[dict[str, str]],
    *,
    run_id: str | None = None,
    moldy_source: str = "trigger",
) -> str:
    """비스트리밍 실행 (트리거용). 최종 응답 텍스트만 반환."""
    # ADR-021 H1 — install the run-scoped secret set so the lazy skill-credential
    # union in ``_prepare_runtime_components`` works and any redaction during the
    # invoke (langfuse mask, persistence) sees the run's real secrets.
    secret_token = set_run_secrets(cfg.secret_values)
    try:
        return await _execute_agent_invoke_inner(
            cfg,
            messages_history,
            run_id=run_id,
            moldy_source=moldy_source,
        )
    finally:
        reset_run_secrets(secret_token)


async def _execute_agent_invoke_inner(
    cfg: AgentConfig,
    messages_history: list[dict[str, str]],
    *,
    run_id: str | None = None,
    moldy_source: str = "trigger",
) -> str:
    agent, lc_messages, config = await _prepare_agent(
        cfg,
        messages_history=messages_history,
        is_trigger_mode=True,
    )

    effective_run_id = run_id or str(_uuid.uuid4())
    langfuse_ctx = build_langfuse_run_context(
        cfg,
        run_id=effective_run_id,
        source=moldy_source,
    )
    config = langfuse_ctx.configure_config(config)

    ctx = _hook_ctx_for_agent(cfg)
    if ctx is not None:
        await hooks.run_pre(ctx)
    started = time.monotonic()

    activate = getattr(langfuse_ctx, "activate", None)
    activation: Any = (
        activate(input_payload={"messages": lc_messages}, output_payload=None)
        if callable(activate)
        else nullcontext()
    )
    try:
        with activation:
            result = await agent.ainvoke({"messages": lc_messages}, config=config)
    except Exception as exc:
        if ctx is not None:
            await hooks.run_failure(ctx, exc)
        raise
    finally:
        langfuse_ctx.flush()

    messages = result.get("messages", [])
    text = ""
    if messages and hasattr(messages[-1], "content"):
        text = messages[-1].content

    if ctx is not None:
        await hooks.run_post(
            ctx,
            HookResult(
                duration_ms=int((time.monotonic() - started) * 1000),
                output=(text[:200] if isinstance(text, str) else None),
            ),
        )
    return text
