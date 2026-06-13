from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from typing import Any, cast

from langgraph.types import Command

from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_stream_mode_chunk,
    adapt_v3_protocol_event,
)
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    format_protocol_sse,
    stored_protocol_event,
    to_protocol_wire_event,
)
from app.agent_runtime.streaming import ArtifactEventRecorder, PersistCallback, StreamErrorRecord

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
    cursor = event["upstream_event_id"] or str(event["seq"])
    return {
        "id": cursor,
        "event": "message",
        "data": to_protocol_wire_event(event),
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


def _nonempty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _artifact_tool_result(event: StoredProtocolEvent) -> tuple[str, str | None] | None:
    if event["method"] != "tools" or not isinstance(event["data"], Mapping):
        return None

    data = event["data"]
    event_name = _nonempty_str(data.get("event"))
    status = _nonempty_str(data.get("status"))
    if event_name not in {"tool-finished", "tool-error"} and status not in {
        "complete",
        "completed",
        "error",
    }:
        return None

    tool_name = (
        _nonempty_str(data.get("tool_name"))
        or _nonempty_str(data.get("name"))
        or _nonempty_str(data.get("tool"))
    )
    if tool_name is None:
        return None
    tool_call_id = _nonempty_str(data.get("tool_call_id")) or _nonempty_str(data.get("id"))
    return tool_name, tool_call_id


def _artifact_protocol_event(
    source_event: StoredProtocolEvent,
    *,
    payload: dict[str, Any],
    seq: int,
    index: int,
) -> StoredProtocolEvent:
    event_id = f"{source_event['id']}:artifact:{index}"
    return stored_protocol_event(
        run_id=source_event["run_id"],
        thread_id=source_event["thread_id"],
        seq=seq,
        method="custom:file_event",
        data=payload,
        namespace=source_event["namespace"],
        event_id=event_id,
        id=event_id,
        timestamp=source_event["timestamp"],
    )


async def _collect_artifact_events(
    event: StoredProtocolEvent,
    *,
    artifact_recorder: ArtifactEventRecorder,
    next_seq: int,
) -> tuple[list[StoredProtocolEvent], int]:
    tool_result = _artifact_tool_result(event)
    if tool_result is None:
        return [], next_seq

    tool_name, tool_call_id = tool_result
    try:
        payloads = await artifact_recorder.collect_after_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )
    except Exception:
        logger.exception(
            "artifact recorder collect failed (run_id=%s, tool=%s)",
            event["run_id"],
            tool_name,
        )
        return [], next_seq

    emitted: list[StoredProtocolEvent] = []
    seq = max(next_seq, event["seq"])
    for index, payload in enumerate(payloads):
        seq += 1
        emitted.append(
            _artifact_protocol_event(
                event,
                payload=dict(payload),
                seq=seq,
                index=index,
            )
        )
    return emitted, seq


async def stream_agent_response_langgraph(
    agent: Any,
    input_: list[Any] | Command | dict[str, Any] | None,
    config: dict[str, Any],
    *,
    trace_sink: list[dict[str, Any]] | None = None,
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
    artifact_seq = 0
    emitted: list[dict[str, Any]] = []

    def emit(event: StoredProtocolEvent) -> str:
        event_dict = dict(event)
        emitted.append(event_dict)
        if trace_sink is not None:
            trace_sink.append(event_dict)
        if broker is not None:
            broker.publish_nowait(_broker_event(event))
        return format_protocol_sse(event)

    if artifact_recorder is not None:
        try:
            await artifact_recorder.prepare()
        except Exception:
            logger.exception("artifact recorder prepare failed (run_id=%s)", msg_id)
            artifact_recorder = None

    try:
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
                if artifact_recorder is not None:
                    artifact_events, artifact_seq = await _collect_artifact_events(
                        event,
                        artifact_recorder=artifact_recorder,
                        next_seq=artifact_seq,
                    )
                    for artifact_event in artifact_events:
                        yield emit(artifact_event)
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
                if artifact_recorder is not None:
                    artifact_events, artifact_seq = await _collect_artifact_events(
                        event,
                        artifact_recorder=artifact_recorder,
                        next_seq=artifact_seq,
                    )
                    for artifact_event in artifact_events:
                        yield emit(artifact_event)
    except Exception as exc:
        record = StreamErrorRecord(error=exc, message=str(exc))
        if error_sink is not None:
            error_sink.append(record)
        fallback_seq += 1
        yield emit(_error_event(run_id=msg_id, thread_id=thread_id, seq=fallback_seq, exc=exc))
    finally:
        if persist_callback is not None and emitted:
            try:
                await persist_callback(emitted)
            except Exception:
                logger.exception("protocol stream persist_callback failed (run_id=%s)", msg_id)
        if broker is not None:
            broker.close()
