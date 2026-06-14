from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from typing import Any, cast

from langgraph.types import Command

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
    resequence_protocol_event,
    stored_protocol_event,
    to_protocol_wire_event,
)
from app.agent_runtime.protocol_persistence import persistable_protocol_event
from app.agent_runtime.protocol_side_effects import (
    collect_protocol_side_effect_events,
    prepare_artifact_recorder,
)
from app.agent_runtime.protocol_usage import collect_protocol_usage_event
from app.agent_runtime.streaming import (
    ArtifactEventRecorder,
    PersistCallback,
    StreamErrorRecord,
)

logger = logging.getLogger(__name__)


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
        data={"message": str(exc)},
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
) -> AsyncGenerator[str, None]:
    msg_id = run_id or str(uuid.uuid4())
    thread_id = _thread_id_from_config(config, msg_id)
    actual_input = _actual_input(input_)
    fallback_seq = 0
    side_effect_seq = 0
    max_emitted_seq = -1
    input_requested_emitted = False
    emitted: list[dict[str, Any]] = []
    persistable_events: list[dict[str, Any]] = []
    seen_usage_keys: set[tuple[str | None, int, int, int, int, float | None]] = set()
    seen_synthesized_tool_call_ids: set[str] = set()

    def emit(event: StoredProtocolEvent) -> str:
        nonlocal input_requested_emitted, max_emitted_seq

        event_to_emit = (
            resequence_protocol_event(event, seq=max_emitted_seq + 1)
            if event["seq"] <= max_emitted_seq
            else event
        )
        max_emitted_seq = event_to_emit["seq"]
        if event_to_emit["method"] == "input.requested":
            input_requested_emitted = True
        event_dict = dict(event_to_emit)
        emitted.append(event_dict)
        persistable_event = persistable_protocol_event(event_to_emit)
        persistable_events.append(persistable_event)
        if trace_sink is not None:
            trace_sink.append(persistable_event)
        if broker is not None:
            broker.publish_nowait(_broker_event(event_to_emit))
        return format_protocol_sse(event_to_emit)

    def emit_canonical_interrupts(event: StoredProtocolEvent) -> list[str]:
        return [
            emit(input_event)
            for input_event in canonical_input_requested_events(
                event,
                first_seq=max_emitted_seq + 1,
            )
        ]

    artifact_recorder = await prepare_artifact_recorder(artifact_recorder, run_id=msg_id)

    try:
        yield emit(
            lifecycle_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=0,
                event="running",
            )
        )
        try:
            stream = await _open_v3_stream(agent, actual_input, config)
            async for raw_event in stream:
                if not isinstance(raw_event, Mapping):
                    fallback_seq += 1
                    yield emit(
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
                yield emit(event)
                for chunk in emit_canonical_interrupts(event):
                    yield chunk
                usage_event, side_effect_seq = collect_protocol_usage_event(
                    event,
                    next_seq=side_effect_seq,
                    seen_keys=seen_usage_keys,
                    usage_sink=usage_sink,
                    cost_per_input_token=cost_per_input_token,
                    cost_per_output_token=cost_per_output_token,
                )
                if usage_event is not None:
                    yield emit(usage_event)
                side_effect_events, side_effect_seq = await collect_protocol_side_effect_events(
                    event,
                    artifact_recorder=artifact_recorder,
                    next_seq=side_effect_seq,
                )
                for side_effect_event in side_effect_events:
                    yield emit(side_effect_event)
                for tool_event in synthesize_tool_events_from_values(
                    event,
                    seen_tool_call_ids=seen_synthesized_tool_call_ids,
                    first_seq=max_emitted_seq + 1,
                ):
                    yield emit(tool_event)
                    side_effect_events, side_effect_seq = await collect_protocol_side_effect_events(
                        tool_event,
                        artifact_recorder=artifact_recorder,
                        next_seq=side_effect_seq,
                    )
                    for side_effect_event in side_effect_events:
                        yield emit(side_effect_event)
        except (AttributeError, NotImplementedError):
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
                yield emit(event)
                for chunk in emit_canonical_interrupts(event):
                    yield chunk
                usage_event, side_effect_seq = collect_protocol_usage_event(
                    event,
                    next_seq=side_effect_seq,
                    seen_keys=seen_usage_keys,
                    usage_sink=usage_sink,
                    cost_per_input_token=cost_per_input_token,
                    cost_per_output_token=cost_per_output_token,
                )
                if usage_event is not None:
                    yield emit(usage_event)
                side_effect_events, side_effect_seq = await collect_protocol_side_effect_events(
                    event,
                    artifact_recorder=artifact_recorder,
                    next_seq=side_effect_seq,
                )
                for side_effect_event in side_effect_events:
                    yield emit(side_effect_event)

        pending_input_events = await pending_input_requested_events(
            agent,
            config,
            run_id=msg_id,
            thread_id=thread_id,
            emitted=emitted,
        )
        for pending_event in pending_input_events:
            yield emit(pending_event)
        yield emit(
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
        record = StreamErrorRecord(error=exc, message=str(exc))
        if error_sink is not None:
            error_sink.append(record)
        yield emit(
            lifecycle_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=max_emitted_seq + 1,
                event="failed",
                error_message=str(exc),
            )
        )
        yield emit(
            _error_event(run_id=msg_id, thread_id=thread_id, seq=max_emitted_seq + 1, exc=exc)
        )
    finally:
        if persist_callback is not None and persistable_events:
            try:
                await persist_callback(persistable_events)
            except Exception:
                logger.exception("protocol stream persist_callback failed (run_id=%s)", msg_id)
        if broker is not None:
            broker.close()
