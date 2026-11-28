"""Direct chat retries through the real OpenAI SSE parser and LangGraph v3 stream."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import httpx2
import openai
import pytest
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeCancelledError

from app.agent_runtime import model_provider_builders
from app.agent_runtime.runtime_component_builder import _build_model_candidates, _prepare_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.stream_retry_authority import StreamAttemptDenied
from app.agent_runtime.stream_retry_policy import PartialModelStreamError, delegation_retry_stopped
from app.config import settings
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)

CONCURRENCY = "Concurrency limit exceeded for user, please retry later"
TIMEOUT = "bad response status code 504"


def _chunk(**delta: Any) -> dict[str, Any]:
    return {
        "object": "chat.completion.chunk",
        "id": "completion-1",
        "created": 0,
        "model": "gpt-4o-mini",
        "choices": [{"delta": delta, "index": 0, "finish_reason": None}],
    }


def _error(message: str) -> dict[str, Any]:
    return {"error": {"type": "openai_error", "message": message}}


def _stream(*frames: dict[str, Any]) -> httpx.Response:
    data = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=data + "data: [DONE]\n\n",
    )


@dataclass
class Gateway:
    respond: Callable[[], httpx.Response]
    models: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    message_roles: tuple[str, ...] = ()

    def handle(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.method == "POST" and request.url.path == "/v1/chat/completions"
        assert payload["stream"] is True
        self.hosts.append(request.url.host)
        self.models.append(payload["model"])
        self.message_roles = tuple(message["role"] for message in payload["messages"])
        return self.respond()


@pytest.fixture
async def gateway(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Gateway]:
    def reject_network(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("provider test attempted to connect outside its mock wire")

    # SDKs can create separate clients for helpers such as moderation. Fail
    # before socket I/O even if a future provider does not use the injected client.
    for library in (httpx, httpx2):
        monkeypatch.setattr(library.AsyncHTTPTransport, "handle_async_request", reject_network)
        monkeypatch.setattr(library.HTTPTransport, "handle_request", reject_network)
    monkeypatch.setattr("app.agent_runtime.runtime.usage_guard.async_session", TestSession)
    wire = Gateway(lambda: _stream(_chunk(content="finished")))
    transport = httpx.MockTransport(wire.handle)
    with httpx.Client(transport=transport) as client:
        async with httpx.AsyncClient(transport=transport) as async_client:
            monkeypatch.setattr(model_provider_builders, "_MODEL_CACHE", OrderedDict())
            monkeypatch.setattr(
                model_provider_builders, "outbound_http_client", lambda **kw: async_client
            )
            monkeypatch.setattr(
                model_provider_builders, "outbound_sync_http_client", lambda **kw: client
            )
            monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", InMemorySaver)
            monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai_compatible"})
            monkeypatch.setattr(
                settings,
                "chat_stream_retry_origins",
                {
                    "openai_compatible": [
                        "https://gateway.test",
                        "https://fallback.test",
                        "https://child.test",
                    ]
                },
            )
            yield wire


def _config(*, explicit_retries: int | None = None) -> AgentConfig:
    configs = (
        [{"type": "model_retry", "params": {"max_retries": explicit_retries}}]
        if explicit_retries is not None
        else []
    )
    return AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="openai_compatible",
        model_name="gpt-4o-mini",
        api_key="test-key",
        base_url="https://gateway.test/v1",
        system_prompt="Answer briefly.",
        tools_config=[],
        thread_id="retry-chat",
        middleware_configs=configs,
        model_fallback_chain=[
            {
                "provider": "openai_compatible",
                "model_name": "gpt-4o",
                "base_url": "https://fallback.test/v1",
            }
        ],
    )


async def _messages(
    graph: Any,
    messages: list[Any],
    config: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    stream = await graph.astream_events({"messages": messages}, config=config, version="v3")
    async for event in stream:
        if event["method"] == "messages":
            frame, _ = event["params"]["data"]
            yield frame


def _text(frames: list[dict[str, Any]]) -> list[str]:
    return [
        frame["delta"]["text"]
        for frame in frames
        if frame.get("delta", {}).get("type") == "text-delta"
    ]


@pytest.mark.parametrize("message", [CONCURRENCY, TIMEOUT])
async def test_early_sse_error_recovers_once_in_100_graph_runs(
    gateway: Gateway, message: str
) -> None:
    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    models = _build_model_candidates(_config())
    retry = DirectStreamRetryMiddleware(fallbacks=models[1:], initial_delay=0)
    graph = create_agent(model=models[0], middleware=[retry])
    gateway.respond = lambda: (
        _stream(_error(message)) if len(gateway.models) % 2 else _stream(_chunk(content="finished"))
    )
    for _ in range(100):
        frames = [frame async for frame in _messages(graph, [{"role": "user", "content": "Hi"}])]
        assert _text(frames) == ["finished"]
        assert sum(frame["event"] == "message-finish" for frame in frames) == 1
    assert gateway.hosts == ["gateway.test", "fallback.test"] * 100


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [
        ("text", openai.APIError),
        ("role", openai.APIError),
        ("tool", openai.APIError),
        ("tool_disconnect", (httpx.ReadError, openai.APIConnectionError)),
        ("unknown", openai.APIError),
        ("401", openai.AuthenticationError),
        ("403", openai.PermissionDeniedError),
    ],
)
async def test_direct_chat_wiring_never_replays_partial_or_denied(
    gateway: Gateway, mode: str, expected_error: type[Exception] | tuple[type[Exception], ...]
) -> None:
    def response() -> httpx.Response:
        if mode in {"401", "403"}:
            return httpx.Response(int(mode), json={"error": {"message": TIMEOUT}})
        if mode == "unknown":
            return _stream(_error("Unsupported response format"))
        if mode == "role":
            return _stream(_chunk(role="assistant"), _error(TIMEOUT))
        if mode == "text":
            return _stream(_chunk(content="partial"), _error(TIMEOUT))
        fragment = {
            "index": 0,
            "id": "tool-1",
            "type": "function",
            "function": {"name": "current_datetime", "arguments": "{"},
        }
        chunk = _chunk(tool_calls=[fragment])
        if mode == "tool_disconnect":

            class InterruptedStream(httpx.AsyncByteStream):
                async def __aiter__(self) -> AsyncIterator[bytes]:
                    yield f"data: {json.dumps(chunk)}\n\n".encode()
                    raise httpx.ReadError("test connection closed")

            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=InterruptedStream()
            )
        return _stream(chunk, _error(CONCURRENCY))

    gateway.respond = response
    graph, history, config = await _prepare_agent(
        _config(explicit_retries=2), messages_history=[{"role": "user", "content": "Hi"}]
    )
    frames: list[dict[str, Any]] = []

    async def consume() -> None:
        async for frame in _messages(graph, history, config):
            frames.append(frame)

    partial = mode in {"text", "role", "tool", "tool_disconnect"}
    with pytest.raises(PartialModelStreamError if partial else expected_error) as caught:
        await consume()
    if partial:
        assert isinstance(caught.value.__cause__, expected_error)
    assert gateway.models == ["gpt-4o-mini"]
    if mode == "text":
        assert _text(frames) == ["partial"]
    elif mode.startswith("tool"):
        tool_starts = [
            frame["content"]
            for frame in frames
            if frame["event"] == "content-block-start"
            and frame["content"]["type"] == "tool_call_chunk"
        ]
        assert [tool["id"] for tool in tool_starts] == ["tool-1"]


async def test_stop_before_retry_does_not_send_again(gateway: Gateway) -> None:
    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    first_send = asyncio.Event()

    def response() -> httpx.Response:
        first_send.set()
        return _stream(_error(TIMEOUT))

    gateway.respond = response
    models = _build_model_candidates(_config())
    graph = create_agent(
        model=models[0],
        middleware=[DirectStreamRetryMiddleware(fallbacks=models[1:], initial_delay=60)],
    )

    async def consume() -> None:
        async for _ in _messages(graph, [{"role": "user", "content": "Hi"}]):
            pass

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(first_send.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
        assert gateway.models == ["gpt-4o-mini"]
    finally:
        task.cancel()


async def test_gateway_exhaustion_honors_configured_total_across_fallback(gateway: Gateway) -> None:
    # A real HTTP 429 also exercises the SDK's built-in retry if it was left
    # enabled. Two application attempts means exactly two wire sends in all.
    gateway.respond = lambda: httpx.Response(
        429, json={"error": {"type": "rate_limit_exceeded", "message": "Too many requests"}}
    )
    graph, history, config = await _prepare_agent(
        _config(explicit_retries=1), messages_history=[{"role": "user", "content": "Hi"}]
    )

    async def consume() -> None:
        async for _ in _messages(graph, history, config):
            pass

    with pytest.raises(openai.RateLimitError):
        await consume()
    assert len(gateway.models) == 2


async def test_retry_after_completed_tool_reuses_result_once(gateway: Gateway) -> None:
    from langchain_core.tools import tool

    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    calls: list[int] = []

    @tool
    def count_items(value: int) -> str:
        """Count a generated batch."""
        calls.append(value)
        return str(value)

    tool_call = {
        "id": "call-1",
        "index": 0,
        "type": "function",
        "function": {"name": "count_items", "arguments": '{"value": 3}'},
    }
    model_calls = iter(
        [
            _stream(_chunk(tool_calls=[tool_call])),
            _stream(_error(TIMEOUT)),
            _stream(_chunk(content="done")),
        ]
    )
    gateway.respond = lambda: next(model_calls)
    models = _build_model_candidates(_config())
    graph = create_agent(
        model=models[0],
        tools=[count_items],
        middleware=[DirectStreamRetryMiddleware(fallbacks=models[1:], initial_delay=0)],
    )
    frames = [frame async for frame in _messages(graph, [{"role": "user", "content": "Count"}])]
    assert _text(frames) == ["done"]
    assert calls == [3]
    assert len(gateway.models) == 3


@pytest.mark.parametrize("mode", ["enabled", "zero_retries", "disabled"])
async def test_gateway_opt_in_and_explicit_retry_limit_in_production_chat(
    gateway: Gateway, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    if mode == "disabled":
        monkeypatch.setattr(settings, "chat_stream_retry_providers", frozenset())
    cfg = _config(explicit_retries=0 if mode == "zero_retries" else None)
    cfg.model_fallback_chain = None
    gateway.respond = lambda: (
        _stream(_error(TIMEOUT))
        if len(gateway.models) == 1
        else _stream(_chunk(content="finished"))
    )
    graph, history, config = await _prepare_agent(
        cfg, messages_history=[{"role": "user", "content": "Hi"}]
    )

    async def collect() -> list[dict[str, Any]]:
        return [frame async for frame in _messages(graph, history, config)]

    if mode == "enabled":
        assert _text(await collect()) == ["finished"]
        assert len(gateway.models) == 2
    else:
        with pytest.raises(openai.APIError):
            await collect()
        assert len(gateway.models) == 1


@pytest.mark.parametrize("explicit", [False, True], ids=["implicit", "explicit"])
async def test_general_purpose_retries_only_its_own_models(
    gateway: Gateway, explicit: bool
) -> None:
    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    cfg = _config(explicit_retries=1)
    expected = ["gpt-4o-mini"] * 4
    if explicit:
        child = _config()
        child.model_name = "explicit-child"
        child.model_fallback_chain = [
            {
                "provider": "openai_compatible",
                "model_name": "explicit-fallback",
                "base_url": "https://child.test/v1",
            }
        ]
        models = _build_model_candidates(child)
        cfg.subagents_config = [
            {
                "name": "general-purpose",
                "description": "Delegate",
                "system_prompt": "Summarize",
                "model": models[0],
                "middleware": [DirectStreamRetryMiddleware(fallbacks=models[1:], initial_delay=0)],
            }
        ]
        expected = ["gpt-4o-mini", "explicit-child", "explicit-fallback", "gpt-4o-mini"]
    call = {
        "index": 0,
        "id": "task-1",
        "type": "function",
        "function": {
            "name": "task",
            "arguments": json.dumps(
                {"subagent_type": "general-purpose", "description": "Summarize"}
            ),
        },
    }
    responses = iter(
        [
            _stream(_chunk(tool_calls=[call])),
            _stream(_error(TIMEOUT)),
            _stream(_chunk(content="Child completed")),
            _stream(_chunk(content="Parent completed")),
        ]
    )
    gateway.respond = lambda: next(responses)
    graph, history, config = await _prepare_agent(
        cfg,
        messages_history=[{"role": "user", "content": "Delegate"}],
    )
    frames = [frame async for frame in _messages(graph, history, config)]
    assert "Child completed" in _text(frames)
    assert _text(frames)[-1] == "Parent completed"
    assert gateway.models == expected


@pytest.mark.parametrize("parent_provider", ["openai_compatible", "openrouter"])
@pytest.mark.parametrize("failure", ["partial", "unknown", "exhausted"])
async def test_parent_tool_retry_cannot_replay_child_side_effect_after_terminal_model_failure(
    gateway: Gateway, parent_provider: str, failure: str
) -> None:
    from langchain_core.tools import tool

    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    effects: list[int] = []

    @tool
    def record_item(number: int) -> str:
        """Record one test item."""
        effects.append(number)
        return "recorded"

    def tool_frame(name: str, args: dict[str, Any]) -> httpx.Response:
        call = {
            "index": 0,
            "id": f"call-{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }
        return _stream(_chunk(tool_calls=[call]))

    def respond() -> httpx.Response:
        model = gateway.models[-1]
        number = gateway.models.count(model)
        if model == "child-effect":
            if "tool" not in gateway.message_roles:
                return tool_frame("record_item", {"number": 7})
            if failure == "partial":
                return _stream(_chunk(content="partial child result"), _error(TIMEOUT))
            return _stream(_error("Unsupported format" if failure == "unknown" else TIMEOUT))
        if number == 1:
            return tool_frame("task", {"subagent_type": "recorder", "description": "Record"})
        return _stream(_chunk(content="Parent done"))

    gateway.respond = respond
    child = _config()
    child.model_name = "child-effect"
    child.model_fallback_chain = None
    cfg = _config()
    cfg.provider = parent_provider
    cfg.model_fallback_chain = None
    cfg.middleware_configs = [{"type": "tool_retry", "params": {"max_retries": 1}}]
    cfg.subagents_config = [
        {
            "name": "recorder",
            "description": "Record an item",
            "system_prompt": "Use the tool once.",
            "tools": [record_item],
            "model": _build_model_candidates(child)[0],
            "middleware": [DirectStreamRetryMiddleware(fallbacks=[], initial_delay=0)],
        }
    ]
    graph, history, config = await _prepare_agent(
        cfg, messages_history=[{"role": "user", "content": "Record one item"}]
    )
    events: list[dict[str, Any]] = []

    async def consume() -> None:
        async for frame in _messages(graph, history, config):
            events.append(frame)

    with pytest.raises(PartialModelStreamError if failure == "partial" else openai.APIError):
        await consume()
    assert effects == [7]
    child_sends = 4 if failure == "exhausted" else 2
    assert gateway.models == ["gpt-4o-mini", *(["child-effect"] * child_sends)]
    assert _text(events).count("partial child result") == int(failure == "partial")


@pytest.mark.parametrize(
    ("stop", "outcome"),
    [(StreamAttemptDenied, "denied"), (asyncio.CancelledError, "cancelled")],
)
async def test_retry_preflight_logs_only_the_request_actually_sent(
    gateway: Gateway, caplog: pytest.LogCaptureFixture, stop: type[BaseException], outcome: str
) -> None:
    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    logger_name = "app.agent_runtime.stream_retry_policy"
    caplog.set_level(logging.INFO, logger=logger_name)
    checks = 0

    async def authorize() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise stop()

    models = _build_model_candidates(_config())
    graph = create_agent(
        model=models[0],
        middleware=[
            DirectStreamRetryMiddleware(
                fallbacks=models[1:], check_authority=authorize, initial_delay=0
            )
        ],
    )
    gateway.respond = lambda: _stream(_error(TIMEOUT))

    async def consume() -> None:
        async for _ in _messages(graph, [{"role": "user", "content": "Hi"}]):
            pass

    # LangGraph records a cancellation raised by an individual node as a
    # NodeCancelledError; a caller-cancelled stream still raises CancelledError.
    expected = NodeCancelledError if stop is asyncio.CancelledError else stop
    with pytest.raises(expected) as result:
        await consume()
    if stop is asyncio.CancelledError:
        assert isinstance(result.value, NodeCancelledError)
        assert isinstance(result.value.__cause__, asyncio.CancelledError)
        assert delegation_retry_stopped(result.value)
    assert checks == 2
    assert gateway.models == ["gpt-4o-mini"]
    assert [record.getMessage() for record in caplog.records if record.name == logger_name] == [
        f"model_stream_attempt attempt=1 output_started=False outcome={outcome}"
    ]


async def test_only_fallback_provider_opted_in_shares_the_attempt_budget(gateway: Gateway) -> None:
    cfg = _config(explicit_retries=2)
    cfg.provider = "openrouter"
    assert "openrouter" not in settings.chat_stream_retry_providers
    responses = iter(
        [
            httpx.Response(429, json={"error": {"message": "Too many requests"}}),
            _stream(_error(TIMEOUT)),
            _stream(_chunk(content="finished")),
        ]
    )
    gateway.respond = lambda: next(responses)
    graph, history, config = await _prepare_agent(
        cfg, messages_history=[{"role": "user", "content": "Hi"}]
    )
    frames = [frame async for frame in _messages(graph, history, config)]
    assert gateway.models == ["gpt-4o-mini", "gpt-4o", "gpt-4o"]
    assert _text(frames) == ["finished"]


@pytest.mark.parametrize(
    ("mode", "base_host", "response_host"),
    [
        ("override", "unknown.test", "unknown.test"),
        ("fallback", "gateway.test", "unknown.test"),
        ("redirect", "gateway.test", "unknown.test"),
        ("redirect", "unknown.test", "gateway.test"),
        ("redirect", "gateway.test", "fallback.test"),
    ],
)
async def test_unverified_origin_stops_sse_retry(
    gateway: Gateway,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    base_host: str,
    response_host: str,
) -> None:
    cfg = _config()
    cfg.base_url = f"https://{base_host}/v1"
    if mode == "fallback":
        cfg.model_fallback_chain = [
            {
                "provider": "openai_compatible",
                "model_name": "gpt-4o",
                "base_url": f"https://{response_host}/v1",
            }
        ]
    if mode == "redirect":
        # Ordinary egress pins redirect-following off. Force it on for this
        # client to check that the SDK reports the final response's origin.
        client = model_provider_builders.outbound_http_client(timeout=5.0)
        monkeypatch.setattr(client, "follow_redirects", True)

    def response() -> httpx.Response:
        if gateway.hosts[-1] == response_host:
            return _stream(_error(TIMEOUT))
        if mode == "redirect":
            return httpx.Response(
                307, headers={"Location": f"https://{response_host}/v1/chat/completions"}
            )
        return httpx.Response(429, json={"error": {"message": "Too many requests"}})

    gateway.respond = response
    graph, history, config = await _prepare_agent(
        cfg, messages_history=[{"role": "user", "content": "Hi"}]
    )
    with pytest.raises(openai.APIError) as error:
        async for _ in _messages(graph, history, config):
            pass
    assert error.value.request.url.host == response_host
    assert gateway.hosts == ([base_host] if mode == "override" else [base_host, response_host])


@pytest.mark.parametrize("original_host", ["unknown.test", "fallback.test", None])
async def test_mutated_or_unpinned_client_does_not_retry_known_frame(
    gateway: Gateway, original_host: str | None
) -> None:
    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    cfg = _config()
    cfg.base_url = f"https://{original_host or 'gateway.test'}/v1"
    primary, fallback = _build_model_candidates(cfg)
    # The original origin must stay out of LangChain run metadata and Pydantic
    # serialization, which are observable to tracers and other middleware.
    assert "_moldy_stream_retry_origin" not in primary.model_dump()
    assert "_moldy_stream_retry_origin" not in (primary.metadata or {})
    if original_host is None:
        # An externally supplied or previously created client has no origin pin.
        object.__delattr__(primary, "_moldy_stream_retry_origin")
    else:
        assert isinstance(primary, ChatOpenAI)
        primary.root_async_client.base_url = "https://gateway.test/v1"
    graph = create_agent(
        model=primary,
        middleware=[DirectStreamRetryMiddleware(fallbacks=[fallback], initial_delay=0)],
    )
    gateway.respond = lambda: _stream(_error(TIMEOUT))
    with pytest.raises(openai.APIError) as error:
        async for _ in _messages(graph, [{"role": "user", "content": "Hi"}]):
            pass
    assert error.value.request.url.host == "gateway.test"
    assert gateway.hosts == ["gateway.test"]


@pytest.mark.parametrize(
    ("http_status", "result_host"),
    [(200, "gateway.test"), (429, "fallback.test")],
    ids=["verified-stream-stays-on-primary", "ordinary-http-uses-fallback"],
)
async def test_unverified_fallback_cannot_receive_in_band_retry(
    gateway: Gateway, monkeypatch: pytest.MonkeyPatch, http_status: int, result_host: str
) -> None:
    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    monkeypatch.setattr(
        settings, "chat_stream_retry_origins", {"openai_compatible": ["https://gateway.test"]}
    )
    primary, fallback = _build_model_candidates(_config())
    graph = create_agent(
        model=primary,
        middleware=[DirectStreamRetryMiddleware(fallbacks=[fallback], initial_delay=0)],
    )
    if http_status == 200:
        failure = _stream(_error(TIMEOUT))
    else:
        failure = httpx.Response(429, json={"error": {"message": "Too many requests"}})
    gateway.respond = lambda: (
        failure if len(gateway.hosts) == 1 else _stream(_chunk(content="done"))
    )
    frames = [frame async for frame in _messages(graph, [{"role": "user", "content": "Hi"}])]
    assert _text(frames) == ["done"]
    assert gateway.hosts == ["gateway.test", result_host]


@pytest.mark.parametrize(
    "change",
    [
        "source_revoked",
        "destination_revoked",
        "source_moved",
        "destination_moved",
        "resource_replaced",
    ],
)
async def test_origin_change_during_stream_backoff_denies_the_pending_send(
    gateway: Gateway, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, change: str
) -> None:
    from app.agent_runtime.runtime.direct_stream_retry import DirectStreamRetryMiddleware

    metric_logger = "app.agent_runtime.stream_retry_policy"
    caplog.set_level(logging.INFO, logger=metric_logger)
    primary, fallback = _build_model_candidates(_config())
    assert isinstance(primary, ChatOpenAI) and isinstance(fallback, ChatOpenAI)
    checks = 0

    async def authorize() -> None:
        nonlocal checks
        checks += 1
        if checks != 2:
            return
        if change in {"source_revoked", "destination_revoked"}:
            remaining = "fallback.test" if change == "source_revoked" else "gateway.test"
            monkeypatch.setattr(
                settings,
                "chat_stream_retry_origins",
                {"openai_compatible": [f"https://{remaining}"]},
            )
        elif change == "resource_replaced":
            foreign = _config()
            foreign.base_url = "https://unknown.test/v1"
            replacement = _build_model_candidates(foreign)[0]
            assert isinstance(replacement, ChatOpenAI)
            fallback.async_client = replacement.async_client
        else:
            changed_model = primary if change == "source_moved" else fallback
            # This host is also allowlisted, but it differs from the model's pinned origin.
            changed_model.root_async_client.base_url = "https://child.test/v1"

    graph = create_agent(
        model=primary,
        middleware=[
            DirectStreamRetryMiddleware(
                fallbacks=[fallback], check_authority=authorize, initial_delay=0
            )
        ],
    )
    gateway.respond = lambda: (
        _stream(_error(TIMEOUT))
        if len(gateway.hosts) == 1
        else _stream(_chunk(content="unexpected"))
    )

    async def consume() -> None:
        async for _ in _messages(graph, [{"role": "user", "content": "Hi"}]):
            pass

    with pytest.raises(openai.APIError) as error:
        await consume()
    assert type(error.value) is openai.APIError
    assert error.value.message == TIMEOUT
    assert error.value.request.url.host == "gateway.test"
    assert checks == 2
    assert gateway.hosts == ["gateway.test"]
    assert [entry.getMessage() for entry in caplog.records if entry.name == metric_logger] == [
        "model_stream_attempt attempt=1 output_started=False outcome=denied"
    ]
