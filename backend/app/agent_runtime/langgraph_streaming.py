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
from app.agent_runtime.streaming import PersistCallback, StreamErrorRecord

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
) -> AsyncGenerator[str, None]:
    msg_id = run_id or str(uuid.uuid4())
    thread_id = _thread_id_from_config(config, msg_id)
    actual_input = _actual_input(input_)
    fallback_seq = 0
    emitted: list[dict[str, Any]] = []

    def emit(event: StoredProtocolEvent) -> str:
        event_dict = dict(event)
        emitted.append(event_dict)
        if trace_sink is not None:
            trace_sink.append(event_dict)
        if broker is not None:
            broker.publish_nowait(_broker_event(event))
        return format_protocol_sse(event)

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
                yield emit(
                    adapt_v3_protocol_event(
                        raw_event,
                        run_id=msg_id,
                        thread_id=thread_id,
                    )
                )
        except (AttributeError, NotImplementedError):
            fallback_seq = 0
            stream = await _open_stream_mode_fallback(agent, actual_input, config)
            async for raw_chunk in stream:
                fallback_seq += 1
                if not isinstance(raw_chunk, tuple | list):
                    raw_chunk = ("custom", raw_chunk)
                yield emit(
                    adapt_stream_mode_chunk(
                        raw_chunk,
                        run_id=msg_id,
                        thread_id=thread_id,
                        seq=fallback_seq,
                    )
                )
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
