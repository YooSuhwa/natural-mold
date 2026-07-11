from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from typing import Any, cast

from langgraph.types import Command

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.langgraph_lifecycle_events import (
    lifecycle_protocol_event,
    terminal_lifecycle_event,
)
from app.agent_runtime.langgraph_pending_inputs import pending_input_requested_events
from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_stream_mode_chunk,
    adapt_v3_protocol_event,
)
from app.agent_runtime.langgraph_tool_event_synthesis import synthesize_tool_events_from_values
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    canonical_input_requested_events,
    format_protocol_sse,
    protocol_event_cursor,
    protocol_interrupts_from_event,
    resequence_protocol_event,
    stored_custom_protocol_event,
    stored_protocol_event,
    to_protocol_wire_event,
)
from app.agent_runtime.protocol_persistence import persistable_wire_protocol_event
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.agent_runtime.protocol_side_effects import (
    collect_protocol_side_effect_events,
    prepare_artifact_recorder,
)
from app.agent_runtime.protocol_usage import collect_protocol_usage_event
from app.agent_runtime.stream_error_messages import public_stream_error_message
from app.agent_runtime.streaming import (
    ArtifactEventRecorder,
    PersistCallback,
    StreamErrorRecord,
)
from app.config import settings

logger = logging.getLogger(__name__)
_FLUSH_BATCH_SIZE = 32
_FLUSH_INTERVAL_SECONDS = 2.0
# 실패 chunk 복원으로 buffer 가 자랄 수 있는 상한 (legacy streaming.py 의
# _MAX_RETRY_BUFFER_EVENTS 와 동일 값). 평균 200B × 5000 = ~1MB. DB 영속
# 장애로 한 turn 의 모든 partial flush 가 실패해도 OOM 보호. 초과 시
# oldest 부터 drop + log (정상 turn 길이 0~수백 events 가정 시 도달은 이상 신호).
_MAX_RETRY_BUFFER_EVENTS = 5000


def _thread_id_from_config(config: Mapping[str, Any], run_id: str) -> str:
    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        thread_id = configurable.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return run_id


def _actual_input(input_: list[Any] | Command | dict[str, Any] | None) -> Any:
    if input_ is None or isinstance(input_, Command | dict):
        return input_
    return {"messages": input_}


async def _await_stream(value: Any) -> AsyncIterator[Any]:
    stream = await value if inspect.isawaitable(value) else value
    return cast(AsyncIterator[Any], stream)


async def _open_v3_stream(
    agent: Any,
    actual_input: Any,
    config: dict[str, Any],
) -> AsyncIterator[Any]:
    return await _await_stream(
        agent.astream_events(
            actual_input,
            config=config,
            version="v3",
        )
    )


async def _open_stream_mode_fallback(
    agent: Any,
    actual_input: Any,
    config: dict[str, Any],
) -> AsyncIterator[Any]:
    return await _await_stream(
        agent.astream(
            actual_input,
            config=config,
            stream_mode=["messages", "updates", "values", "custom"],
            subgraphs=True,
        )
    )


def _broker_event(event: StoredProtocolEvent) -> BrokeredEvent:
    return {
        "id": protocol_event_cursor(event),
        "event": "message",
        "data": dict(to_protocol_wire_event(event)),
    }


def _error_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    exc: Exception,
) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        method="error",
        data={"message": public_stream_error_message(exc)},
    )


def _is_empty_input_requested_event(event: StoredProtocolEvent) -> bool:
    return event["method"] == "input.requested" and not protocol_interrupts_from_event(event)


def _compaction_summarization_event(event: StoredProtocolEvent) -> Mapping[str, Any] | None:
    if event["method"] != "values":
        return None
    data = event["data"]
    if not isinstance(data, Mapping):
        return None
    summarization_event = data.get("_summarization_event")
    return summarization_event if isinstance(summarization_event, Mapping) else None


def _compaction_signal(event: StoredProtocolEvent) -> str | None:
    """Classify the auto-compaction signal in an adapted v3 protocol event.

    deepagents 0.6.9 streams its summarization tokens on the ``messages`` channel
    tagged ``metadata.lc_source == "summarization"`` (these must be suppressed so
    the summary text never leaks into the answer), and commits the compaction via
    a ``_summarization_event`` on the ``values`` channel. Returns
    ``"summary_token"`` / ``"committed"`` / ``None``.
    """
    method = event["method"]
    data = event["data"]
    if method == "messages" and isinstance(data, Mapping):
        metadata = data.get("metadata")
        if isinstance(metadata, Mapping) and metadata.get("lc_source") == "summarization":
            return "summary_token"
    summarization_event = _compaction_summarization_event(event)
    if summarization_event is not None:
        cutoff_index = summarization_event.get("cutoff_index")
        if isinstance(cutoff_index, int) and cutoff_index > 0:
            return "committed"
    return None


def _compaction_offload_path(event: StoredProtocolEvent, thread_id: str) -> str | None:
    summarization_event = _compaction_summarization_event(event)
    if summarization_event is not None:
        file_path = summarization_event.get("file_path")
        if isinstance(file_path, str) and file_path:
            return file_path
    # FilesystemBackend(virtual_mode=True) offloads deterministically here when the
    # event omits an explicit ``file_path`` (runtime_component_builder).
    return f"/conversation_history/{thread_id}.md" if thread_id else None


def _compaction_cutoff_index(event: StoredProtocolEvent) -> int | None:
    summarization_event = _compaction_summarization_event(event)
    if summarization_event is not None:
        cutoff_index = summarization_event.get("cutoff_index")
        if isinstance(cutoff_index, int):
            return cutoff_index
    return None


def _compaction_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    state: str,
    offload_path: str | None = None,
    cutoff_index: int | None = None,
) -> StoredProtocolEvent:
    payload: dict[str, Any] = {"state": state}
    if offload_path is not None:
        payload["offload_path"] = offload_path
    if cutoff_index is not None:
        payload["cutoff_index"] = cutoff_index
    # Stable event id (``run:compaction:<state>``) so a reload replay dedupes the
    # marker instead of stacking duplicates — same contract as memory/artifact
    # side-effect events.
    stable_id = f"{run_id}:compaction:{state}"
    return stored_custom_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        name=event_names.COMPACTION,
        payload=payload,
        event_id=stable_id,
        id=stable_id,
    )


def _subagent_names_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    display_names: Mapping[str, str],
) -> StoredProtocolEvent:
    """Emit the subagent runtime_name -> display_name map once at stream head.

    The v3 path streams deepagents ``task`` tool calls whose ``subagent_type`` is
    the runtime name (``agent_<8hex>``); the frontend SDK uses it verbatim as the
    subagent card title. Rewriting the checkpoint-backed ``subagent_type`` would
    corrupt execution + namespace binding + reload seeding, so we instead ship the
    map as a ``custom`` side-channel event and let the frontend substitute a
    human-readable name at the display layer only. Stable event id dedupes on
    replay/reload (same contract as the compaction/memory/artifact side-effects).
    """
    stable_id = f"{run_id}:subagent_names"
    return stored_custom_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        name=event_names.SUBAGENT_NAMES,
        payload={"names": dict(display_names)},
        event_id=stable_id,
        id=stable_id,
    )


def _annotate_session_consent_eligibility(
    input_event: StoredProtocolEvent,
    consent_tools: list[str],
) -> None:
    """AD-4 — 인터럽트 wire의 review_configs에 세션 동의 가능 플래그를 주석한다.

    langchain ``ReviewConfig`` 는 ``action_name``/``allowed_decisions`` 만
    만들므로(여분 키 미보존), 우리 wire 계층에서 주입한다. 승인 카드가 이
    플래그를 보고 "이 세션에서 계속 허용" 옵션을 조건부 렌더한다. dict를
    in-place 수정 — persist 경로에도 같은 페이로드가 실려 replay가 유지된다.
    """

    data = input_event.get("data")
    if not isinstance(data, dict):
        return
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return
    review_configs = payload.get("review_configs")
    if not isinstance(review_configs, list):
        return
    eligible = set(consent_tools)
    for config in review_configs:
        if isinstance(config, dict) and config.get("action_name") in eligible:
            config["session_consent_eligible"] = True


def _skill_draft_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    brief: dict[str, Any],
) -> StoredProtocolEvent:
    """Emit the skill-builder draft summary once at stream head (AD-5).

    Payload는 요약만 싣는다(세션 id/모드/slug/파일 경로·크기/변경 수) — 파일
    내용은 도구 결과/FS 읽기로만 흐른다(§6-7). Stable event id dedupes on
    replay/reload (same contract as ``_memory_recalled_event``).
    """
    stable_id = f"{run_id}:skill_draft"
    return stored_custom_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        name=event_names.SKILL_DRAFT,
        payload=brief,
        event_id=stable_id,
        id=stable_id,
    )


def _memory_recalled_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    memories: list[dict[str, Any]],
) -> StoredProtocolEvent:
    """Emit the recalled long-term memory briefs once at stream head (W2-3).

    The component builder injects up to N memory records into the system
    prompt silently; this side-channel makes the recall visible so the chat
    can render a "기억 N개 참고" chip. Stable event id dedupes on
    replay/reload (same contract as ``_subagent_names_event``).
    """
    stable_id = f"{run_id}:memory_recalled"
    return stored_custom_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        name=event_names.MEMORY_RECALLED,
        payload={"memories": memories},
        event_id=stable_id,
        id=stable_id,
    )


async def stream_agent_response_langgraph(
    agent: Any,
    input_: list[Any] | Command | dict[str, Any] | None,
    config: dict[str, Any],
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    cost_per_input_token: float | None = None,
    cost_per_output_token: float | None = None,
    usage_sink: dict[str, Any] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: EventBroker | None = None,
    persist_callback: PersistCallback | None = None,
    run_id: str | None = None,
    artifact_recorder: ArtifactEventRecorder | None = None,
    subagent_display_names: dict[str, str] | None = None,
    recalled_memories: list[dict[str, Any]] | None = None,
    skill_draft_brief: dict[str, Any] | None = None,
    session_consent_tools: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    msg_id = run_id or str(uuid.uuid4())
    thread_id = _thread_id_from_config(config, msg_id)
    actual_input = _actual_input(input_)
    fallback_seq = 0
    side_effect_seq = 0
    max_emitted_seq = -1
    input_requested_emitted = False
    deferred_empty_input_requested: StoredProtocolEvent | None = None
    emitted: list[dict[str, Any]] = []
    persist_buffer: list[dict[str, Any]] = []
    last_persist_flush_at = time.monotonic()
    seen_usage_keys: set[tuple[str | None, int, int, int, int, float | None]] = set()
    seen_synthesized_tool_call_ids: set[str] = set()
    # Auto-compaction marker — one running + one done per run (dedup flags). When
    # the flag is off, summarization tokens flow through unchanged (legacy).
    compaction_enabled = settings.compaction_marker_enabled
    compaction_running_emitted = False
    compaction_done_emitted = False
    # 스트리밍 timing — 스트림 시작부터 첫 텍스트 토큰(messages 채널)까지(TTFT) +
    # 총 생성시간. usage 이벤트에 실려 같은 경로로 흐른다.
    stream_started_at = time.monotonic()
    first_token_at: float | None = None

    background_persist_tasks: set[asyncio.Task[None]] = set()

    async def _persist_chunk(events: list[dict[str, Any]]) -> None:
        """Background flush body — swallow exceptions so a DB hiccup doesn't
        kill the live stream. 실패한 chunk는 buffer **앞**에 복원해 이벤트
        순서를 보존한 채 다음 threshold flush / finally 최종 flush에서
        재시도한다 (기존 inline flush와 동일 재시도 의미론)."""
        nonlocal persist_buffer
        if persist_callback is None:
            return
        try:
            await persist_callback(events)
        except Exception:
            persist_buffer = [*events, *persist_buffer]
            logger.exception("protocol stream persist_callback failed (run_id=%s)", msg_id)
            if len(persist_buffer) > _MAX_RETRY_BUFFER_EVENTS:
                overflow = len(persist_buffer) - _MAX_RETRY_BUFFER_EVENTS
                del persist_buffer[:overflow]
                logger.warning(
                    "persist buffer overflow (run_id=%s) — dropped %d oldest "
                    "events to stay under cap=%d",
                    msg_id,
                    overflow,
                    _MAX_RETRY_BUFFER_EVENTS,
                )

    def schedule_persist_flush() -> None:
        """BE-P5(e): fire-and-forget partial flush — 기존 inline ``await`` 는
        32 events/2초마다 DB 라운드트립 동안 토큰 방출을 블로킹했다. legacy
        ``streaming.py`` 의 create_task 패턴을 따르되 in-flight 한도는 1:
        run 내 flush 를 직렬화해 chunk ``seq_start`` 단조성과 run-scoped
        seen-event-id 캐시(BE-P5(d))의 무경합을 보장한다. in-flight 중에는
        buffer 가 계속 쌓이고 다음 emit / finally 에서 재검사된다."""
        nonlocal last_persist_flush_at, persist_buffer
        if persist_callback is None or not persist_buffer or background_persist_tasks:
            return
        elapsed = time.monotonic() - last_persist_flush_at
        should_wait_for_batch = len(persist_buffer) < _FLUSH_BATCH_SIZE
        should_wait_for_interval = elapsed < _FLUSH_INTERVAL_SECONDS
        if should_wait_for_batch and should_wait_for_interval:
            return
        events = persist_buffer
        persist_buffer = []
        last_persist_flush_at = time.monotonic()
        task = asyncio.create_task(_persist_chunk(events))
        background_persist_tasks.add(task)
        task.add_done_callback(background_persist_tasks.discard)

    async def emit(event: StoredProtocolEvent) -> str:
        nonlocal input_requested_emitted, max_emitted_seq

        event_to_emit = (
            resequence_protocol_event(event, seq=max_emitted_seq + 1)
            if event["seq"] <= max_emitted_seq
            else event
        )
        max_emitted_seq = event_to_emit["seq"]
        if event_to_emit["method"] == "input.requested":
            input_requested_emitted = True
        wire_event: StoredProtocolEvent = {
            **event_to_emit,
            "data": redact_protocol_data(
                event_to_emit["method"],
                event_to_emit["data"],
                redact_memory=False,
            ),
        }
        event_dict = dict(wire_event)
        emitted.append(event_dict)
        # BE-P5(b): persist 는 wire 1회 redaction 결과를 재사용하고 compact +
        # memory 마스킹만 얹는다 (이벤트당 전체 재귀 redaction 2회 → 1회).
        # 비-memory·비-compact 이벤트는 persistable/wire/emitted/broker 가 같은
        # data 객체를 공유한다 — legacy 와 동일한 "emit 이후 누구도 mutate 하지
        # 않는다" 불변식에 의존. egress 변환을 새로 넣는다면 in-place 수정 금지
        # (copy 후 수정), 아니면 persist 된 DB row 까지 오염된다.
        persistable_event = persistable_wire_protocol_event(wire_event)
        persist_buffer.append(persistable_event)
        if trace_sink is not None:
            trace_sink.append(persistable_event)
        if broker is not None:
            broker.publish_nowait(_broker_event(wire_event))
        schedule_persist_flush()
        return format_protocol_sse(wire_event)

    async def emit_canonical_interrupts(event: StoredProtocolEvent) -> list[str]:
        chunks: list[str] = []
        for input_event in canonical_input_requested_events(
            event,
            first_seq=max_emitted_seq + 1,
        ):
            if session_consent_tools:
                _annotate_session_consent_eligibility(input_event, session_consent_tools)
            chunks.append(await emit(input_event))
        return chunks

    artifact_recorder = await prepare_artifact_recorder(artifact_recorder, run_id=msg_id)

    try:
        yield await emit(
            lifecycle_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=0,
                event="running",
            )
        )
        if subagent_display_names:
            side_effect_seq += 1
            yield await emit(
                _subagent_names_event(
                    run_id=msg_id,
                    thread_id=thread_id,
                    seq=side_effect_seq,
                    display_names=subagent_display_names,
                )
            )
        if recalled_memories:
            side_effect_seq += 1
            yield await emit(
                _memory_recalled_event(
                    run_id=msg_id,
                    thread_id=thread_id,
                    seq=side_effect_seq,
                    memories=recalled_memories,
                )
            )
        if skill_draft_brief:
            side_effect_seq += 1
            yield await emit(
                _skill_draft_event(
                    run_id=msg_id,
                    thread_id=thread_id,
                    seq=side_effect_seq,
                    brief=skill_draft_brief,
                )
            )
        try:
            stream = await _open_v3_stream(agent, actual_input, config)
        except (AttributeError, NotImplementedError):
            # Fallback path: only reached for stream-mode-only agents (test fakes
            # that lack ``astream_events`` v3). Unlike the v3 path it intentionally
            # omits the deferred ``_is_empty_input_requested_event`` normalization —
            # stream-mode chunks never carry a standalone empty ``input.requested``
            # method, so there is nothing to defer here. Pending interrupts are still
            # recovered uniformly via ``pending_input_requested_events`` below.
            fallback_seq = 0
            stream = await _open_stream_mode_fallback(agent, actual_input, config)
            async for raw_chunk in stream:
                fallback_seq += 1
                if not isinstance(raw_chunk, tuple | list):
                    raw_chunk = ("custom", raw_chunk)
                event = adapt_stream_mode_chunk(
                    raw_chunk,
                    run_id=msg_id,
                    thread_id=thread_id,
                    seq=fallback_seq,
                )
                if compaction_enabled:
                    signal = _compaction_signal(event)
                    if signal == "summary_token":
                        if not compaction_running_emitted:
                            compaction_running_emitted = True
                            side_effect_seq += 1
                            yield await emit(
                                _compaction_event(
                                    run_id=msg_id,
                                    thread_id=thread_id,
                                    seq=side_effect_seq,
                                    state="running",
                                )
                            )
                        continue
                    if signal == "committed" and not compaction_done_emitted:
                        compaction_done_emitted = True
                        side_effect_seq += 1
                        yield await emit(
                            _compaction_event(
                                run_id=msg_id,
                                thread_id=thread_id,
                                seq=side_effect_seq,
                                state="done",
                                offload_path=_compaction_offload_path(event, thread_id),
                                cutoff_index=_compaction_cutoff_index(event),
                            )
                        )
                yield await emit(event)
                for chunk in await emit_canonical_interrupts(event):
                    yield chunk
                if first_token_at is None and event["method"] == "messages":
                    first_token_at = time.monotonic()
                usage_event, side_effect_seq = collect_protocol_usage_event(
                    event,
                    next_seq=side_effect_seq,
                    seen_keys=seen_usage_keys,
                    usage_sink=usage_sink,
                    cost_per_input_token=cost_per_input_token,
                    cost_per_output_token=cost_per_output_token,
                    started_at=stream_started_at,
                    first_token_at=first_token_at,
                )
                if usage_event is not None:
                    yield await emit(usage_event)
                side_effect_events, side_effect_seq = await collect_protocol_side_effect_events(
                    event,
                    artifact_recorder=artifact_recorder,
                    next_seq=side_effect_seq,
                )
                for side_effect_event in side_effect_events:
                    yield await emit(side_effect_event)
        else:
            async for raw_event in stream:
                if not isinstance(raw_event, Mapping):
                    fallback_seq += 1
                    yield await emit(
                        stored_protocol_event(
                            run_id=msg_id,
                            thread_id=thread_id,
                            seq=fallback_seq,
                            method="custom",
                            data={"payload": repr(raw_event)},
                        )
                    )
                    continue
                event = adapt_v3_protocol_event(
                    raw_event,
                    run_id=msg_id,
                    thread_id=thread_id,
                )
                if _is_empty_input_requested_event(event):
                    deferred_empty_input_requested = event
                    continue
                if compaction_enabled:
                    signal = _compaction_signal(event)
                    if signal == "summary_token":
                        # ★ Suppress: summarization tokens must never reach the
                        # client (they would render as ghost/answer text). Emit the
                        # transient "running" marker once on the first such token.
                        if not compaction_running_emitted:
                            compaction_running_emitted = True
                            side_effect_seq += 1
                            yield await emit(
                                _compaction_event(
                                    run_id=msg_id,
                                    thread_id=thread_id,
                                    seq=side_effect_seq,
                                    state="running",
                                )
                            )
                        continue
                    if signal == "committed" and not compaction_done_emitted:
                        compaction_done_emitted = True
                        side_effect_seq += 1
                        yield await emit(
                            _compaction_event(
                                run_id=msg_id,
                                thread_id=thread_id,
                                seq=side_effect_seq,
                                state="done",
                                offload_path=_compaction_offload_path(event, thread_id),
                                cutoff_index=_compaction_cutoff_index(event),
                            )
                        )
                        # The values event itself still flows below (state sync).
                yield await emit(event)
                for chunk in await emit_canonical_interrupts(event):
                    yield chunk
                if first_token_at is None and event["method"] == "messages":
                    first_token_at = time.monotonic()
                usage_event, side_effect_seq = collect_protocol_usage_event(
                    event,
                    next_seq=side_effect_seq,
                    seen_keys=seen_usage_keys,
                    usage_sink=usage_sink,
                    cost_per_input_token=cost_per_input_token,
                    cost_per_output_token=cost_per_output_token,
                    started_at=stream_started_at,
                    first_token_at=first_token_at,
                )
                if usage_event is not None:
                    yield await emit(usage_event)
                side_effect_events, side_effect_seq = await collect_protocol_side_effect_events(
                    event,
                    artifact_recorder=artifact_recorder,
                    next_seq=side_effect_seq,
                )
                for side_effect_event in side_effect_events:
                    yield await emit(side_effect_event)
                for tool_event in synthesize_tool_events_from_values(
                    event,
                    seen_tool_call_ids=seen_synthesized_tool_call_ids,
                    first_seq=max_emitted_seq + 1,
                ):
                    yield await emit(tool_event)
                    side_effect_events, side_effect_seq = await collect_protocol_side_effect_events(
                        tool_event,
                        artifact_recorder=artifact_recorder,
                        next_seq=side_effect_seq,
                    )
                    for side_effect_event in side_effect_events:
                        yield await emit(side_effect_event)

        pending_input_events = await pending_input_requested_events(
            agent,
            config,
            run_id=msg_id,
            thread_id=thread_id,
            emitted=emitted,
        )
        for pending_event in pending_input_events:
            yield await emit(pending_event)
        if not pending_input_events and deferred_empty_input_requested is not None:
            yield await emit(deferred_empty_input_requested)
        yield await emit(
            lifecycle_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=max_emitted_seq + 1,
                event=terminal_lifecycle_event(
                    has_pending_input=input_requested_emitted or bool(pending_input_events)
                ),
            )
        )
    except Exception as exc:
        record = StreamErrorRecord(error=exc, message=public_stream_error_message(exc))
        if error_sink is not None:
            error_sink.append(record)
        yield await emit(
            lifecycle_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=max_emitted_seq + 1,
                event="failed",
                error_message=record.message,
            )
        )
        yield await emit(
            _error_event(run_id=msg_id, thread_id=thread_id, seq=max_emitted_seq + 1, exc=exc)
        )
    finally:
        # BE-P5(e) — background flush join → 최종 flush → broker close. 무조건
        # 실행 (정상 종료 / 예외 / 클라이언트 disconnect 시 generator aclose()
        # 모두). 순서가 중요: (1) in-flight task join — 실패 chunk 가 buffer
        # 앞에 복원되어 잔여분이 확정된다. (2) 잔여 buffer 를 마지막으로 직접
        # flush — 여기서도 실패하면 그때만 영구 손실 (log).
        if background_persist_tasks:
            await asyncio.gather(*background_persist_tasks, return_exceptions=True)
        if persist_callback is not None and persist_buffer:
            final_events = persist_buffer
            persist_buffer = []
            try:
                await persist_callback(final_events)
            except Exception:
                logger.exception(
                    "final flush persist_callback failed (run_id=%s) — %d events permanently lost",
                    msg_id,
                    len(final_events),
                )
        if broker is not None:
            broker.close()
