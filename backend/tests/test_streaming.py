"""Tests for app.agent_runtime.streaming — SSE formatting and stream logic."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_runtime.streaming import format_sse, stream_agent_response


def _combine_deltas(events: list[str]) -> str:
    """content_delta 이벤트들의 delta를 결합하여 전체 텍스트 반환."""
    return "".join(
        json.loads(e.split("data: ")[1].strip())["delta"]
        for e in events
        if "content_delta" in e
    )

# ---------------------------------------------------------------------------
# format_sse
# ---------------------------------------------------------------------------


def test_format_sse_basic():
    result = format_sse("content_delta", {"delta": "Hello"})
    assert result.startswith("event: content_delta\n")
    assert "data: " in result
    assert result.endswith("\n\n")
    data = json.loads(result.split("data: ")[1].strip())
    assert data["delta"] == "Hello"


def test_format_sse_unicode():
    result = format_sse("content_delta", {"delta": "안녕하세요"})
    data = json.loads(result.split("data: ")[1].strip())
    assert data["delta"] == "안녕하세요"


def test_format_sse_complex_data():
    payload = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}, "content": "done"}
    result = format_sse("message_end", payload)
    data = json.loads(result.split("data: ")[1].strip())
    assert data["usage"]["prompt_tokens"] == 10


# ---------------------------------------------------------------------------
# Helpers for mock agent
# ---------------------------------------------------------------------------


def _make_ai_chunk(content: str, usage_metadata: dict | None = None) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.type = "ai"
    msg.tool_calls = []
    msg.usage_metadata = usage_metadata
    return msg


def _make_tool_call_chunk(tool_name: str, args: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = ""
    msg.type = "ai"
    msg.tool_calls = [{"name": tool_name, "args": args}]
    msg.usage_metadata = None
    return msg


def _make_tool_result_chunk(tool_name: str, result: str) -> MagicMock:
    msg = MagicMock()
    msg.content = result
    msg.type = "tool"
    msg.name = tool_name
    msg.tool_calls = []
    msg.usage_metadata = None
    return msg


class MockAgent:
    """Fake agent that yields predefined chunks from astream()."""

    def __init__(self, chunks: list[tuple[MagicMock, dict]]):
        self._chunks = chunks

    async def astream(self, input: Any, config: Any = None, **kwargs: Any):
        for chunk in self._chunks:
            yield chunk


# ---------------------------------------------------------------------------
# stream_agent_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_message_start_and_end():
    """Stream always starts with message_start and ends with message_end."""
    agent = MockAgent([])
    events = [e async for e in stream_agent_response(agent, [], {})]

    assert len(events) == 2
    assert "message_start" in events[0]
    assert "message_end" in events[-1]


@pytest.mark.asyncio
async def test_stream_content_delta():
    ai_chunk = _make_ai_chunk("Hello world")
    agent = MockAgent([(ai_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]

    assert _combine_deltas(events) == "Hello world"


@pytest.mark.asyncio
async def test_stream_multiple_content_deltas():
    chunks = [
        (_make_ai_chunk("Hello "), {}),
        (_make_ai_chunk("world!"), {}),
    ]
    agent = MockAgent(chunks)

    events = [e async for e in stream_agent_response(agent, [], {})]

    assert _combine_deltas(events) == "Hello world!"

    # message_end should contain full concatenated content
    end_event = [e for e in events if "message_end" in e][0]
    end_data = json.loads(end_event.split("data: ")[1].strip())
    assert end_data["content"] == "Hello world!"


@pytest.mark.asyncio
async def test_stream_tool_call_start():
    tc_chunk = _make_tool_call_chunk("web_search", {"query": "weather"})
    agent = MockAgent([(tc_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]

    tc_events = [e for e in events if "tool_call_start" in e]
    assert len(tc_events) == 1
    data = json.loads(tc_events[0].split("data: ")[1].strip())
    assert data["tool_name"] == "web_search"
    assert data["parameters"]["query"] == "weather"


@pytest.mark.asyncio
async def test_stream_tool_call_result():
    result_chunk = _make_tool_result_chunk("web_search", "search results here")
    agent = MockAgent([(result_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]

    result_events = [e for e in events if "tool_call_result" in e]
    assert len(result_events) == 1
    data = json.loads(result_events[0].split("data: ")[1].strip())
    assert data["tool_name"] == "web_search"
    assert data["result"] == "search results here"


@pytest.mark.asyncio
async def test_stream_usage_metadata():
    usage = {"input_tokens": 100, "output_tokens": 50}
    ai_chunk = _make_ai_chunk("done", usage_metadata=usage)
    agent = MockAgent([(ai_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]

    end_event = [e for e in events if "message_end" in e][0]
    end_data = json.loads(end_event.split("data: ")[1].strip())
    assert end_data["usage"]["prompt_tokens"] == 100
    assert end_data["usage"]["completion_tokens"] == 50


@pytest.mark.asyncio
async def test_stream_exception_yields_error():
    """If the agent raises an exception, an error event should be emitted."""

    class ErrorAgent:
        async def astream(self, *args, **kwargs):
            raise RuntimeError("LLM connection failed")
            yield  # make it an async generator  # noqa: E501

    agent = ErrorAgent()
    events = [e async for e in stream_agent_response(agent, [], {})]

    error_events = [e for e in events if "error" in e and "message_start" not in e]
    assert len(error_events) == 1
    data = json.loads(error_events[0].split("data: ")[1].strip())
    assert "LLM connection failed" in data["message"]


@pytest.mark.asyncio
async def test_stream_full_flow():
    """Full flow: AI message -> tool call -> tool result -> AI message."""
    chunks = [
        (_make_ai_chunk("Let me search..."), {}),
        (_make_tool_call_chunk("web_search", {"query": "test"}), {}),
        (_make_tool_result_chunk("web_search", "Found results"), {}),
        (_make_ai_chunk("Here are the results"), {}),
    ]
    agent = MockAgent(chunks)

    events = [e async for e in stream_agent_response(agent, [], {})]

    event_types = []
    for e in events:
        if "message_start" in e:
            event_types.append("message_start")
        elif "content_delta" in e:
            event_types.append("content_delta")
        elif "tool_call_start" in e:
            event_types.append("tool_call_start")
        elif "tool_call_result" in e:
            event_types.append("tool_call_result")
        elif "message_end" in e:
            event_types.append("message_end")

    # Character-level streaming produces multiple content_delta events per chunk.
    # Verify ordering: message_start → content_deltas → tool events → content_deltas → message_end
    assert event_types[0] == "message_start"
    assert event_types[-1] == "message_end"
    # tool_call_start and tool_call_result appear between content_delta groups
    tc_start_idx = event_types.index("tool_call_start")
    tc_result_idx = event_types.index("tool_call_result")
    assert tc_start_idx < tc_result_idx
    # Content deltas appear before tool call and after tool result
    assert "content_delta" in event_types[1:tc_start_idx]
    assert "content_delta" in event_types[tc_result_idx + 1 : -1]
