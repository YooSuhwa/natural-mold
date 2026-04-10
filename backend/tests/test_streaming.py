"""Tests for app.agent_runtime.streaming — SSE formatting and stream logic."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_runtime.streaming import _is_tool_selector_json, format_sse, stream_agent_response

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

    content_events = [e for e in events if "content_delta" in e]
    # streaming.py batches deltas per LLM chunk (with middleware JSON filtering)
    assert len(content_events) >= 1
    concatenated = "".join(
        json.loads(e.split("data: ")[1].strip())["delta"] for e in content_events
    )
    assert concatenated == "Hello world"


@pytest.mark.asyncio
async def test_stream_multiple_content_deltas():
    chunks = [
        (_make_ai_chunk("Hello "), {}),
        (_make_ai_chunk("world!"), {}),
    ]
    agent = MockAgent(chunks)

    events = [e async for e in stream_agent_response(agent, [], {})]

    content_events = [e for e in events if "content_delta" in e]
    # streaming.py batches deltas per LLM chunk
    assert len(content_events) >= 2
    concatenated = "".join(
        json.loads(e.split("data: ")[1].strip())["delta"] for e in content_events
    )
    assert concatenated == "Hello world!"

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

    # Deduplicate consecutive content_deltas (chunk-level streaming)
    deduped = []
    for et in event_types:
        if not deduped or deduped[-1] != et:
            deduped.append(et)

    assert deduped == [
        "message_start",
        "content_delta",
        "tool_call_start",
        "tool_call_result",
        "content_delta",
        "message_end",
    ]


# ---------------------------------------------------------------------------
# _is_tool_selector_json
# ---------------------------------------------------------------------------


def test_is_tool_selector_json_true():
    assert _is_tool_selector_json('{"tools":["web_search","scraper"]}') is True


def test_is_tool_selector_json_false_multiple_keys():
    assert _is_tool_selector_json('{"tools":[],"extra":"val"}') is False


def test_is_tool_selector_json_false_not_list():
    assert _is_tool_selector_json('{"tools":"not_a_list"}') is False


def test_is_tool_selector_json_false_invalid():
    assert _is_tool_selector_json("not json") is False


def test_is_tool_selector_json_false_empty_dict():
    assert _is_tool_selector_json("{}") is False


# ---------------------------------------------------------------------------
# stream_agent_response — middleware JSON filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_filters_middleware_json():
    """Middleware JSON like {"tools":["a"]} should be filtered out."""
    ai_chunk = _make_ai_chunk('Before{"tools":["web_search"]}After')
    agent = MockAgent([(ai_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]

    content_events = [e for e in events if "content_delta" in e]
    concatenated = "".join(
        json.loads(e.split("data: ")[1].strip())["delta"] for e in content_events
    )
    assert '{"tools"' not in concatenated
    assert "Before" in concatenated
    assert "After" in concatenated


@pytest.mark.asyncio
async def test_stream_preserves_normal_json():
    """Normal JSON that is NOT {"tools":[...]} should NOT be filtered."""
    ai_chunk = _make_ai_chunk('Here is {"result":"ok"} data')
    agent = MockAgent([(ai_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]

    content_events = [e for e in events if "content_delta" in e]
    concatenated = "".join(
        json.loads(e.split("data: ")[1].strip())["delta"] for e in content_events
    )
    assert '{"result":"ok"}' in concatenated.replace(" ", "").replace('"result"', '"result"')


# ---------------------------------------------------------------------------
# stream_agent_response — estimated cost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_estimated_cost():
    """When cost rates are provided, estimated_cost is calculated."""
    usage = {"input_tokens": 1000, "output_tokens": 500}
    ai_chunk = _make_ai_chunk("done", usage_metadata=usage)
    agent = MockAgent([(ai_chunk, {})])

    events = [
        e async for e in stream_agent_response(
            agent, [], {},
            cost_per_input_token=0.00001,
            cost_per_output_token=0.00003,
        )
    ]

    end_event = [e for e in events if "message_end" in e][0]
    end_data = json.loads(end_event.split("data: ")[1].strip())
    assert "estimated_cost" in end_data["usage"]
    # 1000 * 0.00001 + 500 * 0.00003 = 0.01 + 0.015 = 0.025
    assert abs(end_data["usage"]["estimated_cost"] - 0.025) < 0.0001


# ---------------------------------------------------------------------------
# stream_agent_response — incomplete JSON buffer flush
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_flushes_incomplete_json():
    """Incomplete JSON in buffer at end should be flushed as content."""
    ai_chunk = _make_ai_chunk('Text{incomplete')
    agent = MockAgent([(ai_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]

    end_event = [e for e in events if "message_end" in e][0]
    end_data = json.loads(end_event.split("data: ")[1].strip())
    # The incomplete JSON buffer should be in the final content
    assert "Text{incomplete" in end_data["content"]
