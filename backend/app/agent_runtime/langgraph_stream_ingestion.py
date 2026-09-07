from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from langgraph.types import Command

from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_stream_mode_chunk,
    adapt_v3_protocol_event,
)
from app.agent_runtime.mcp_app_projection import attach_verified_mcp_apps
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_custom_protocol_event

StreamSource = Literal["v3", "stream-mode"]


@dataclass(frozen=True, slots=True)
class IngestedProtocolEvent:
    """A normalized event plus the upstream stream contract that produced it."""

    event: StoredProtocolEvent
    source: StreamSource


def thread_id_from_config(config: Mapping[str, Any], run_id: str) -> str:
    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        thread_id = configurable.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return run_id


def actual_input(input_: list[Any] | Command | dict[str, Any] | None) -> Any:
    if input_ is None or isinstance(input_, Command | dict):
        return input_
    return {"messages": input_}


async def _await_stream(value: Any) -> AsyncIterator[Any]:
    stream = await value if inspect.isawaitable(value) else value
    return cast(AsyncIterator[Any], stream)


async def open_v3_stream(
    agent: Any,
    input_: Any,
    config: dict[str, Any],
) -> AsyncIterator[Any]:
    return await _await_stream(agent.astream_events(input_, config=config, version="v3"))


async def open_stream_mode_fallback(
    agent: Any,
    input_: Any,
    config: dict[str, Any],
) -> AsyncIterator[Any]:
    return await _await_stream(
        agent.astream(
            input_,
            config=config,
            stream_mode=["messages", "updates", "values", "custom"],
            subgraphs=True,
        )
    )


async def iter_ingested_protocol_events(
    agent: Any,
    input_: Any,
    config: dict[str, Any],
    *,
    run_id: str,
    thread_id: str,
) -> AsyncIterator[IngestedProtocolEvent]:
    """Normalize v3 or fallback chunks without exposing opaque upstream values."""

    try:
        stream = await open_v3_stream(agent, input_, config)
    except (AttributeError, NotImplementedError):
        stream = await open_stream_mode_fallback(agent, input_, config)
        seq = 0
        async for raw_chunk in stream:
            seq += 1
            chunk = raw_chunk if isinstance(raw_chunk, tuple | list) else ("custom", raw_chunk)
            event = adapt_stream_mode_chunk(
                chunk,
                run_id=run_id,
                thread_id=thread_id,
                seq=seq,
            )
            yield IngestedProtocolEvent(
                event=await attach_verified_mcp_apps(
                    event,
                    raw_chunk,
                    expected_conversation_id=thread_id,
                    expected_run_id=run_id,
                ),
                source="stream-mode",
            )
        return

    malformed_seq = 0
    async for raw_event in stream:
        if isinstance(raw_event, Mapping):
            event = adapt_v3_protocol_event(raw_event, run_id=run_id, thread_id=thread_id)
            event = await attach_verified_mcp_apps(
                event,
                raw_event,
                expected_conversation_id=thread_id,
                expected_run_id=run_id,
            )
        else:
            malformed_seq += 1
            event = stored_custom_protocol_event(
                run_id=run_id,
                thread_id=thread_id,
                seq=malformed_seq,
                name="malformed",
                payload=None,
            )
        yield IngestedProtocolEvent(event=event, source="v3")
