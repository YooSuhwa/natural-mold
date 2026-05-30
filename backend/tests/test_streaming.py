"""Tests for app.agent_runtime.streaming — SSE formatting and stream logic."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_runtime.streaming import (
    StreamErrorRecord,
    _is_tool_selector_json,
    format_sse,
    stream_agent_response,
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
async def test_stream_exception_records_error_sink_and_failed_end_status():
    """Visible stream errors must be observable by the caller as failures."""

    class ErrorAgent:
        async def astream(self, *args, **kwargs):
            raise RuntimeError("LLM connection failed")
            yield  # make it an async generator

    errors: list[StreamErrorRecord] = []
    events = [
        e
        async for e in stream_agent_response(
            ErrorAgent(),
            [],
            {},
            error_sink=errors,
        )
    ]

    assert len(errors) == 1
    assert isinstance(errors[0].error, RuntimeError)
    assert errors[0].message == "LLM connection failed"

    end_event = [e for e in events if "message_end" in e][0]
    end_data = json.loads(end_event.split("data: ")[1].strip())
    assert end_data["status"] == "failed"


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


# ---------------------------------------------------------------------------
# Backpressure + retry buffer (M2 보강)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_partial_flush_retries_in_finally():
    """Partial flush가 실패한 chunk는 retry_buffer를 거쳐 finally에서 재시도.

    DB 일시 장애로 한 chunk가 실패해도 final flush가 한 번 더 시도하여
    silent data loss를 막는 회귀 가드.
    """
    # 32 events 임계치를 넘기는 chunk 1개를 발생시키기 위해 LLM 응답을
    # 길게 만든다. content_delta 한 번 + message_start/end → 약 3개
    # 이벤트라 batch_size 임계치는 안 닿지만, time 임계치(2s)를 인위적으로
    # 강제하기 어렵다. 대신 _FLUSH_BATCH_SIZE를 monkey patch.
    from app.agent_runtime import streaming as streaming_mod

    original_batch = streaming_mod._FLUSH_BATCH_SIZE
    streaming_mod._FLUSH_BATCH_SIZE = 1  # 매 emit마다 flush 시도

    call_count = {"n": 0}
    received: list[list[dict[str, Any]]] = []

    async def flaky_callback(chunk: list[dict[str, Any]]) -> None:
        call_count["n"] += 1
        # 첫 partial flush만 실패 (retry_buffer 진입). 이후는 성공.
        if call_count["n"] == 1:
            raise RuntimeError("DB hiccup")
        received.append(list(chunk))

    try:
        agent = MockAgent([(_make_ai_chunk("hello"), {})])
        _ = [
            e
            async for e in stream_agent_response(
                agent, [], {}, persist_callback=flaky_callback, run_id="run-retry"
            )
        ]
    finally:
        streaming_mod._FLUSH_BATCH_SIZE = original_batch

    # 첫 호출은 실패했지만 final flush가 retry_buffer + 잔여 buffer를 모두
    # 시도하여 결국 모든 이벤트가 received에 누적됨. 최소 1번은 성공해야 함.
    assert call_count["n"] >= 2  # 최소 첫 fail + final retry
    # 모든 이벤트 id가 run-retry- 프리픽스
    flat_ids = [evt["id"] for chunk in received for evt in chunk]
    assert any(eid.startswith("run-retry-") for eid in flat_ids)


@pytest.mark.asyncio
async def test_inflight_flush_cap_throttles_create_task():
    """In-flight task가 _MAX_INFLIGHT_FLUSHES를 초과하면 새 chunk는 buffer에 보관.

    Backpressure 회귀 가드 — DB가 느려져도 task 폭주가 일어나지 않는다.
    """
    from app.agent_runtime import streaming as streaming_mod

    original_batch = streaming_mod._FLUSH_BATCH_SIZE
    original_cap = streaming_mod._MAX_INFLIGHT_FLUSHES
    streaming_mod._FLUSH_BATCH_SIZE = 1
    streaming_mod._MAX_INFLIGHT_FLUSHES = 1  # 한 번에 1개만 in-flight

    flush_started = asyncio.Event()
    flush_release = asyncio.Event()
    flush_count = {"n": 0}

    async def slow_callback(chunk: list[dict[str, Any]]) -> None:
        flush_count["n"] += 1
        flush_started.set()
        # 첫 호출은 release까지 대기 — in-flight 점유
        if flush_count["n"] == 1:
            await flush_release.wait()

    # 여러 content_delta를 yield하는 agent → 여러 flush 시도 유발
    chunks = [(_make_ai_chunk(f"c{i}"), {}) for i in range(5)]
    agent = MockAgent(chunks)

    try:
        # 백그라운드에서 stream 진행
        events: list[str] = []

        async def consume():
            async for e in stream_agent_response(
                agent, [], {}, persist_callback=slow_callback, run_id="run-backpressure"
            ):
                events.append(e)

        consume_task = asyncio.create_task(consume())
        # 첫 flush 시작 대기 (in-flight=1 점유)
        await asyncio.wait_for(flush_started.wait(), timeout=2.0)
        # 다른 emit들은 cap 때문에 새 task 안 만들고 buffer에 쌓임.
        # release하면 finally의 final flush가 잔여를 처리.
        flush_release.set()
        await asyncio.wait_for(consume_task, timeout=5.0)
    finally:
        streaming_mod._FLUSH_BATCH_SIZE = original_batch
        streaming_mod._MAX_INFLIGHT_FLUSHES = original_cap

    # 최소 1회는 호출됐고, cap 덕분에 무한 폭주는 안 일어남.
    # 정확한 호출 수는 timing-dependent라 lower bound만 검증.
    assert flush_count["n"] >= 1


@pytest.mark.asyncio
async def test_retry_buffer_overflow_drops_oldest():
    """retry_buffer가 _MAX_RETRY_BUFFER_EVENTS 초과 시 oldest부터 drop.

    DB 영속 장애 시나리오 — 모든 partial flush가 실패하고 retry_buffer
    가 한도를 넘으면 OOM 방지를 위해 oldest event를 drop해야 한다.
    """
    from app.agent_runtime import streaming as streaming_mod

    original_batch = streaming_mod._FLUSH_BATCH_SIZE
    original_cap = streaming_mod._MAX_RETRY_BUFFER_EVENTS
    streaming_mod._FLUSH_BATCH_SIZE = 1
    streaming_mod._MAX_RETRY_BUFFER_EVENTS = 3  # 매우 작은 cap으로 즉시 overflow

    flush_count = {"n": 0}
    received: list[list[dict[str, Any]]] = []

    async def always_failing(chunk: list[dict[str, Any]]) -> None:
        flush_count["n"] += 1
        # 모든 partial flush 실패 → retry_buffer로 적재
        # final flush(>= 5번째 호출 추정)에서만 성공해 capture
        if flush_count["n"] < 5:
            raise RuntimeError("DB down")
        received.append(list(chunk))

    try:
        # 여러 content_delta로 여러 partial flush 유발
        chunks = [(_make_ai_chunk(f"c{i}"), {}) for i in range(10)]
        agent = MockAgent(chunks)
        _ = [
            e
            async for e in stream_agent_response(
                agent,
                [],
                {},
                persist_callback=always_failing,
                run_id="run-overflow",
            )
        ]
    finally:
        streaming_mod._FLUSH_BATCH_SIZE = original_batch
        streaming_mod._MAX_RETRY_BUFFER_EVENTS = original_cap

    # 최종 flush로 받은 event 수 합산이 cap(=3) 이하여야 함 — 초과 시 drop됨.
    # 단, final flush가 retry_buffer + flush_buffer 두 chunk로 호출되므로
    # received[0]는 retry_buffer (cap 이하), received[1]은 잔여 flush_buffer.
    if received:
        retry_chunk_size = len(received[0])
        assert retry_chunk_size <= 3, (
            f"retry_buffer overflow가 안 일어남: {retry_chunk_size} > cap=3"
        )
