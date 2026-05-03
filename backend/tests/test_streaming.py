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


def test_format_sse_with_event_id_emits_id_line():
    result = format_sse("content_delta", {"delta": "hi"}, event_id="msg-1-3")
    assert "id: msg-1-3\n" in result
    # event/id/data 순서 — 클라이언트 파서가 라인 단위로 처리하므로 무관하지만
    # 서버 형식 일관성을 위해 검증.
    lines = result.strip().split("\n")
    assert lines[0] == "event: content_delta"
    assert lines[1] == "id: msg-1-3"
    assert lines[2].startswith("data: ")


def test_format_sse_without_event_id_omits_id_line():
    result = format_sse("content_delta", {"delta": "hi"})
    assert "id: " not in result
    assert result.startswith("event: content_delta\ndata: ")


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
    # cache_creation/cache_read는 details가 없으면 0으로 채워진다.
    assert end_data["usage"]["cache_creation_tokens"] == 0
    assert end_data["usage"]["cache_read_tokens"] == 0


@pytest.mark.asyncio
async def test_stream_usage_metadata_with_cache_tokens():
    """LangChain ``usage_metadata.input_token_details``의 cache 토큰을 평탄화한다."""
    usage = {
        "input_tokens": 1200,
        "output_tokens": 80,
        "input_token_details": {
            "cache_creation": 800,
            "cache_read": 300,
        },
    }
    ai_chunk = _make_ai_chunk("done", usage_metadata=usage)
    agent = MockAgent([(ai_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]
    end_event = [e for e in events if "message_end" in e][0]
    end_data = json.loads(end_event.split("data: ")[1].strip())

    assert end_data["usage"]["prompt_tokens"] == 1200
    assert end_data["usage"]["completion_tokens"] == 80
    assert end_data["usage"]["cache_creation_tokens"] == 800
    assert end_data["usage"]["cache_read_tokens"] == 300


@pytest.mark.asyncio
async def test_stream_emits_unique_event_ids_per_chunk():
    """모든 SSE chunk는 ``id: {msg_id}-{seq}`` 형태의 고유 id를 갖는다.

    클라이언트가 dedup 또는 stale 폐기에 사용. seq는 단조 증가.
    """
    chunks = [
        (_make_ai_chunk("Hello "), {}),
        (_make_tool_call_chunk("web_search", {"q": "weather"}), {}),
        (_make_tool_result_chunk("web_search", "result"), {}),
        (_make_ai_chunk("done", usage_metadata={"input_tokens": 5, "output_tokens": 3}), {}),
    ]
    agent = MockAgent(chunks)
    events = [e async for e in stream_agent_response(agent, [], {})]

    ids: list[str] = []
    for raw in events:
        for line in raw.split("\n"):
            if line.startswith("id: "):
                ids.append(line[4:])

    # 모든 SSE 이벤트가 id를 갖고, 모두 unique
    assert len(ids) == len(events), "every emitted SSE event must carry an id line"
    assert len(set(ids)) == len(ids), "event ids must be unique across the stream"

    # 형식: ``{uuid}-{seq}``, seq는 1..N
    msg_id = ids[0].rsplit("-", 1)[0]
    seqs = [int(i.rsplit("-", 1)[1]) for i in ids]
    assert seqs == list(range(1, len(ids) + 1))
    # 모두 같은 message id 접두사
    assert all(i.startswith(f"{msg_id}-") for i in ids)


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
        e
        async for e in stream_agent_response(
            agent,
            [],
            {},
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
    ai_chunk = _make_ai_chunk("Text{incomplete")
    agent = MockAgent([(ai_chunk, {})])

    events = [e async for e in stream_agent_response(agent, [], {})]

    end_event = [e for e in events if "message_end" in e][0]
    end_data = json.loads(end_event.split("data: ")[1].strip())
    # The incomplete JSON buffer should be in the final content
    assert "Text{incomplete" in end_data["content"]


# ---------------------------------------------------------------------------
# W3-out M2 — broker dual-write + partial flush + run_id injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_run_id_injection_uses_external_id():
    """``run_id`` 가 주어지면 message_start의 ``id`` 와 SSE event id 의 prefix
    가 그 값으로 강제된다."""
    agent = MockAgent([(_make_ai_chunk("hi"), {})])
    forced_run_id = "deadbeef-1234"

    events = [
        e async for e in stream_agent_response(agent, [], {}, run_id=forced_run_id)
    ]

    start_event = [e for e in events if "message_start" in e][0]
    start_data = json.loads(start_event.split("data: ")[1].strip())
    assert start_data["id"] == forced_run_id
    # SSE id 라인도 ``{run_id}-<seq>`` 패턴.
    assert f"id: {forced_run_id}-1" in start_event


@pytest.mark.asyncio
async def test_stream_dual_writes_to_broker_and_trace_sink():
    """broker.publish_nowait + trace_sink.append 가 같은 이벤트 시퀀스를 받는다."""
    from app.agent_runtime.event_broker import EventBroker

    chunks = [
        (_make_ai_chunk("a"), {}),
        (_make_ai_chunk("b"), {}),
    ]
    agent = MockAgent(chunks)
    broker = EventBroker("run-x")
    trace_sink: list[dict[str, Any]] = []

    events = [
        e
        async for e in stream_agent_response(
            agent,
            [],
            {},
            trace_sink=trace_sink,
            broker=broker,
            run_id="run-x",
        )
    ]
    assert len(events) >= 2  # message_start + message_end at minimum

    # Broker is closed in finally.
    assert broker.is_closed is True
    # last_event_id is the message_end event.
    assert broker.last_event_id is not None
    assert broker.last_event_id.startswith("run-x-")

    # trace_sink and broker buffer agree on event ids (subset; broker buffer
    # is bounded, but sink stores all).
    sink_ids = [e["id"] for e in trace_sink]
    buffer_ids = [e["id"] for e in broker._buffer]  # noqa: SLF001
    # Every buffered id is in trace_sink (trace_sink is the source of truth).
    assert set(buffer_ids).issubset(set(sink_ids))


@pytest.mark.asyncio
async def test_stream_persist_callback_final_flush_in_finally():
    """persist_callback 이 주어지면 stream 종료 시 최소한 한 번은 호출된다
    (final flush in finally block)."""
    agent = MockAgent([(_make_ai_chunk("hi"), {})])
    captured_chunks: list[list[dict[str, Any]]] = []

    async def callback(chunk: list[dict[str, Any]]) -> None:
        captured_chunks.append(list(chunk))

    _ = [
        e
        async for e in stream_agent_response(
            agent, [], {}, persist_callback=callback, run_id="run-y"
        )
    ]

    # 짧은 stream (이벤트 < 32, 시간 < 2s) 이라 fire-and-forget partial flush
    # 는 안 트리거되지만 finally 의 final flush 가 한 번은 호출되어야 한다.
    assert len(captured_chunks) >= 1
    # 모든 캡처된 이벤트 id 는 ``run-y-`` 프리픽스.
    flat_ids = [evt["id"] for chunk in captured_chunks for evt in chunk]
    assert all(eid.startswith("run-y-") for eid in flat_ids)


@pytest.mark.asyncio
async def test_stream_broker_close_called_even_on_exception():
    """astream 이 예외를 던져도 broker.close() 가 finally 에서 실행된다."""
    from app.agent_runtime.event_broker import EventBroker

    class FailingAgent:
        async def astream(self, *args: Any, **kwargs: Any):
            yield (_make_ai_chunk("partial"), {})
            raise RuntimeError("boom")

        async def aget_state(self, *args: Any, **kwargs: Any):
            state = MagicMock()
            state.tasks = []
            return state

    agent = FailingAgent()
    broker = EventBroker("run-fail")

    # streaming.py 는 예외를 emit("error") 로 변환하므로 generator 가 깔끔하게
    # 종료된다. 우리는 finally 의 broker.close 만 검증.
    _ = [
        e
        async for e in stream_agent_response(
            agent, [], {}, broker=broker, run_id="run-fail"
        )
    ]
    assert broker.is_closed is True
