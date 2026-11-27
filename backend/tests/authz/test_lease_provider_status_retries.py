"""HTTP 503 and mid-stream failures stay one send per permit (internal I12, slice I12a)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import ModuleType
from typing import Any

import httpx
import openai
import pytest

from app.agent_runtime.lease_provider_retry_policy import (
    LeaseProviderRetryPolicy,
    is_retryable_provider_error,
)
from tests.authz.lease_provider_wire import (
    PROVIDERS,
    Wire,
    build,
    drive,
    guard_for,
    install_wire,
    status_503,
)
from tests.authz.test_lease_provider_retries import GOOGLE_RESPONSE, _openai_chunk, _sse

RETRY_TWICE = LeaseProviderRetryPolicy(max_attempts=2, initial_delay=0.5)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return install_wire(monkeypatch)


def _kinds(wire: Wire) -> list[str]:
    return [event[0] for event in wire.events]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ainvoke", "astream"])
@pytest.mark.parametrize("case", sorted(PROVIDERS))
async def test_http_503_without_application_retry_sends_once(
    wire: Wire, case: str, mode: str
) -> None:
    wire.handler = status_503
    owned = build(case, guard_for(wire))
    with pytest.raises(Exception) as caught:  # noqa: PT011 — provider-specific 503 types
        await drive(owned, mode)
    assert is_retryable_provider_error(caught.value), type(caught.value)
    assert _kinds(wire) == ["open", "send", "close"]
    await owned.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ainvoke", "astream"])
@pytest.mark.parametrize("case", sorted(PROVIDERS))
async def test_http_503_with_retry_policy_uses_distinct_permits(
    wire: Wire, case: str, mode: str
) -> None:
    wire.handler = status_503
    owned = build(case, guard_for(wire, policy=RETRY_TWICE))
    with pytest.raises(Exception) as caught:  # noqa: PT011 — provider-specific 503 types
        await drive(owned, mode)
    assert is_retryable_provider_error(caught.value)
    assert _kinds(wire) == ["open", "send", "close", "sleep", "open", "send", "close"]
    assert [event[1] for event in wire.events if event[0] == "open"] == [1, 2]
    await owned.aclose()


def _first_chunk_then_read_error(request: Any, lib: ModuleType) -> Any:
    is_google = "generativelanguage" in request.url.host
    first = _sse(GOOGLE_RESPONSE, done=False) if is_google else _sse(_openai_chunk("h"), done=False)

    async def body() -> AsyncIterator[bytes]:
        yield first
        raise lib.ReadError("connection reset mid-stream", request=request)

    return lib.Response(200, headers={"content-type": "text/event-stream"}, content=body())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["openai", "google"])
async def test_stream_error_after_first_chunk_is_not_retried(wire: Wire, case: str) -> None:
    wire.handler = _first_chunk_then_read_error
    owned = build(case, guard_for(wire, policy=RETRY_TWICE))
    chunks: list[Any] = []

    async def consume() -> None:
        async for chunk in owned.model.astream("hello"):
            chunks.append(chunk)

    with pytest.raises(Exception) as caught:  # noqa: PT011 — transport error type varies by SDK
        await consume()
    assert chunks, "the first chunk must arrive before the failure"
    assert is_retryable_provider_error(caught.value)
    assert wire.sends == 1 and wire.opened == 1
    assert "sleep" not in _kinds(wire)
    await owned.aclose()


@pytest.mark.parametrize("case", ["text", "tool", "role", "tool_disconnect"])
async def test_opted_in_gateway_never_replays_an_emitted_stream_chunk(
    monkeypatch: pytest.MonkeyPatch, wire: Wire, case: str
) -> None:
    from app.agent_runtime.stream_retry_policy import PartialModelStreamError
    from app.config import settings

    monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai"})
    first = _openai_chunk("hello" if case == "text" else "")
    if case.startswith("tool"):
        first["choices"][0]["delta"]["tool_calls"] = [
            {
                "index": 0,
                "id": "call1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }
        ]
    gateway = {"error": {"message": "bad response status code 504", "type": "openai_error"}}

    def handler(request: Any, lib: ModuleType) -> Any:
        async def body() -> AsyncIterator[bytes]:
            yield _sse(first, done=False)
            if case == "tool_disconnect":
                raise lib.ReadError("connection reset", request=request)
            yield _sse(gateway, done=False)

        return lib.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    wire.handler = handler
    owned = build("openai", guard_for(wire, policy=RETRY_TWICE))
    try:
        delivered: list[Any] = []

        async def consume() -> None:
            async for chunk in owned.model.astream("hello"):
                delivered.append(chunk)

        with pytest.raises(PartialModelStreamError) as caught:
            await consume()
        cause_type = httpx.ReadError if case == "tool_disconnect" else openai.APIError
        assert isinstance(caught.value.__cause__, cause_type)
        assert caught.value.code == "partial_model_stream"
        assert len(delivered) == 1
        if case.startswith("tool"):
            assert delivered[0].tool_call_chunks[0]["id"] == "call1"
        assert _kinds(wire) == ["open", "send", "close"]
    finally:
        await owned.aclose()


async def test_durable_cancel_during_stream_retry_backoff_prevents_next_permit(
    monkeypatch: pytest.MonkeyPatch, wire: Wire
) -> None:
    from app.agent_runtime import lease_bound_model
    from app.config import settings

    monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai"})
    checks = 0
    attempts = []

    def record(**event: Any) -> None:
        attempts.append(event)

    monkeypatch.setattr(lease_bound_model, "record_stream_attempt", record)

    async def check(*, prepared: bool) -> None:
        nonlocal checks
        assert prepared
        checks += 1
        if checks == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(lease_bound_model, "check_stream_attempt", check)
    wire.handler = status_503
    owned = build("openai", guard_for(wire, policy=RETRY_TWICE))
    try:
        with pytest.raises(asyncio.CancelledError):
            await drive(owned, "astream")
        assert checks == 2
        assert attempts == [{"attempt": 1, "output_started": False, "outcome": "cancelled"}]
        assert _kinds(wire) == ["open", "send", "close", "sleep"]
    finally:
        await owned.aclose()


@pytest.mark.parametrize(
    ("enabled", "stream", "expected_calls"),
    [(True, True, 1), (True, False, 2), (False, True, 2), (False, False, 2)],
)
async def test_empty_reply_middleware_respects_lease_attempt_budget(
    monkeypatch: pytest.MonkeyPatch, wire: Wire, enabled: bool, stream: bool, expected_calls: int
) -> None:
    from langchain.agents.middleware import ModelRequest
    from langchain_core.messages import AIMessage

    from app.agent_runtime.runtime.reliability import EmptyContentRetryMiddleware
    from app.config import settings
    from tests.authz.test_lease_provider_retries import OPENAI_COMPLETION

    monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai"} if enabled else set())

    def empty_response(_: Any, lib: ModuleType) -> Any:
        if stream:
            return lib.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse(_openai_chunk(""), done=True),
            )
        completion = {
            **OPENAI_COMPLETION,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
        }
        return lib.Response(200, json=completion)

    wire.handler = empty_response
    owned = build("openai", guard_for(wire))

    async def empty(_: Any) -> AIMessage:
        messages = await drive(owned, "astream" if stream else "ainvoke")
        return AIMessage(content="".join(str(message.content) for message in messages))

    try:
        middleware = EmptyContentRetryMiddleware(max_retries=1)
        await middleware.awrap_model_call(ModelRequest(model=owned.model, messages=[]), empty)
        assert wire.sends == expected_calls
    finally:
        await owned.aclose()


@pytest.mark.parametrize(
    ("message", "code", "kind"),
    [
        ("permission denied", 429, "permission_denied"),
        ("unknown", 504, "openai_error"),
        ("input exceeds the context window", 504, "openai_error"),
    ],
)
async def test_stream_body_numeric_code_is_not_a_transient_http_status(
    monkeypatch: pytest.MonkeyPatch, wire: Wire, message: str, code: int, kind: str
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai"})
    frame = {"error": {"message": message, "code": code, "type": kind}}

    def handler(_: Any, lib: ModuleType) -> Any:
        return lib.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse(frame, done=False)
        )

    wire.handler = handler
    owned = build("openai", guard_for(wire, policy=RETRY_TWICE))
    try:
        with pytest.raises(openai.APIError):
            await drive(owned, "astream")
        assert _kinds(wire) == ["open", "send", "close"]
    finally:
        await owned.aclose()
