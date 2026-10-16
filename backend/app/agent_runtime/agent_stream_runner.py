"""Legacy SSE stream/invoke runner — compatibility facade.

Implementation lives in ``agent_stream_hooks`` (hook context/result),
``agent_stream_execution`` (prepare + hook + Langfuse stream body) and
``agent_invoke_runner`` (trigger invoke). Tests patch this module's
attributes (``_prepare_agent``, ``hooks``, ``stream_agent_response``,
``build_langfuse_run_context`` ...) after import; the extracted bodies resolve
them here at call time via ``agent_stream_facade_bindings``.
"""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

from app.agent_runtime.agent_invoke_runner import (
    _execute_agent_invoke_inner,
    execute_agent_invoke,
    execute_prepared_agent_invoke,
)
from app.agent_runtime.agent_stream_execution import (
    _USE_PREPPED_LC_MESSAGES,
    _run_agent_stream,
    _run_prepared_agent_stream,
    _stream_with_secrets,
)
from app.agent_runtime.agent_stream_hooks import _hook_ctx_for_agent, _hook_result_from_usage
from app.agent_runtime.message_utils import RuntimeMessage, content_to_text
from app.agent_runtime.offload_protocol_projection import project_offload_egress_data
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets
from app.agent_runtime.runtime_component_builder import _prepare_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord, stream_agent_response
from app.hooks import HookContext, HookResult, hooks
from app.observability.langfuse import LangfuseTraceRecord, build_langfuse_run_context

# Legacy import names kept on the facade (patching stdlib names here does not reach moved bodies).
__all__ = ["HookContext", "HookResult", "UTC", "asyncio", "content_to_text", "datetime"]
__all__ += ["_execute_agent_invoke_inner", "_hook_ctx_for_agent", "_hook_result_from_usage"]
__all__ += ["_prepare_agent", "_stream_with_secrets", "build_langfuse_run_context"]
__all__ += ["execute_agent_invoke", "hooks", "nullcontext", "project_offload_egress_data"]
__all__ += ["redact_protocol_data", "reset_run_secrets", "set_run_secrets"]
__all__ += ["stream_agent_response", "time", "_uuid"]
# Opt-in prepared-lease (Task 10 I20) entry points used by I16-I19.
__all__ += ["_run_prepared_agent_stream", "execute_prepared_agent_invoke"]


async def execute_agent_stream(
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
