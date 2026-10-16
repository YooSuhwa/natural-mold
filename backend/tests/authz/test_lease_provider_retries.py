"""Application retry, permit lifetime and fallback permits for lease models (I12a)."""

from __future__ import annotations

import json
from types import ModuleType
from typing import Any

import pytest

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.lease_provider_retry_policy import (
    LeaseProviderRetryPolicy,
    is_retryable_provider_error,
)
from tests.authz.lease_provider_wire import (
    PROVIDERS,
    Wire,
    _connect_error,
    build,
    drive,
    guard_for,
    install_wire,
)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return install_wire(monkeypatch)


RETRY_TWICE = LeaseProviderRetryPolicy(max_attempts=2, initial_delay=0.5)

OPENAI_COMPLETION = {
    "id": "c1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o-mini",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
GOOGLE_RESPONSE = {
    "candidates": [
        {"content": {"role": "model", "parts": [{"text": "hi"}]}, "finishReason": "STOP"}
    ],
    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
}


def _openai_chunk(content: str) -> dict[str, Any]:
    delta = {"role": "assistant", "content": content}
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


def _sse(*payloads: dict[str, Any], done: bool) -> bytes:
    body = "".join(f"data: {json.dumps(payload)}\n\n" for payload in payloads)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


def _success(request: Any, lib: ModuleType) -> Any:
    is_google = "generativelanguage" in request.url.host
    streaming = "stream" in request.url.path or b'"stream": true' in request.content
    streaming = streaming or b'"stream":true' in request.content
    if not streaming:
        payload = GOOGLE_RESPONSE if is_google else OPENAI_COMPLETION
        return lib.Response(200, json=payload)
    headers = {"content-type": "text/event-stream"}
    if is_google:
        return lib.Response(200, headers=headers, content=_sse(GOOGLE_RESPONSE, done=False))
    chunks = _sse(_openai_chunk("h"), _openai_chunk("i"), done=True)
    return lib.Response(200, headers=headers, content=chunks)


def _fail_then_succeed(wire: Wire) -> None:
    calls = {"n": 0}

    def handler(request: Any, lib: ModuleType) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return _connect_error(request, lib)
        return _success(request, lib)

    wire.handler = handler


def test_policy_backoff_is_bounded_and_classifies_errors() -> None:
    policy = LeaseProviderRetryPolicy(max_attempts=4, initial_delay=1, max_delay=3)
    assert [policy.delay_for(n) for n in (1, 2, 3)] == [1, 2, 3]
    assert policy.should_retry(ConnectionError(), 3) and not policy.should_retry(TimeoutError(), 4)
    assert not is_retryable_provider_error(DispatchDeniedError("organization_archived"))
    assert not is_retryable_provider_error(ValueError("bad request"))
    with pytest.raises(ValueError, match="max_attempts"):
        LeaseProviderRetryPolicy(max_attempts=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ainvoke", "astream"])
@pytest.mark.parametrize("case", ["openai", "google"])
async def test_application_retry_uses_a_distinct_permit_after_backoff(
    wire: Wire, case: str, mode: str
) -> None:
    _fail_then_succeed(wire)
    owned = build(case, guard_for(wire, policy=RETRY_TWICE))
    result = await drive(owned, mode)
    assert "".join(str(item.content) for item in result) == "hi"
    kinds = [event[0] for event in wire.events]
    assert kinds == ["open", "send", "close", "sleep", "open", "send", "close"]
    assert [event[1] for event in wire.events if event[0] == "open"] == [1, 2]
    await owned.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ainvoke", "astream"])
@pytest.mark.parametrize("case", sorted(PROVIDERS))
async def test_revocation_during_backoff_stops_at_one_send(
    wire: Wire, case: str, mode: str
) -> None:
    wire.deny_from = 2
    owned = build(case, guard_for(wire, policy=RETRY_TWICE))
    with pytest.raises(DispatchDeniedError) as caught:
        await drive(owned, mode)
    assert caught.value.code == "organization_archived"
    assert wire.sends == 1
    assert [event[0] for event in wire.events] == ["open", "send", "close", "sleep"]
    await owned.aclose()


@pytest.mark.asyncio
async def test_denied_first_permit_sends_nothing(wire: Wire) -> None:
    wire.deny_from = 1
    owned = build("anthropic", guard_for(wire, policy=RETRY_TWICE))
    with pytest.raises(DispatchDeniedError):
        await owned.model.ainvoke("hello")
    assert wire.events == []
    await owned.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["openai", "google"])
async def test_stream_permit_closes_after_eager_establishment(wire: Wire, case: str) -> None:
    wire.handler = _success
    owned = build(case, guard_for(wire))
    seen: list[tuple[Any, ...]] = []
    async for chunk in owned.model.astream("hello"):
        seen.append(("chunk", list(wire.events)))
        assert chunk is not None
    first_view = seen[0][1]
    assert [event[0] for event in first_view] == ["open", "send", "close"]
    assert wire.sends == 1 and wire.opened == 1
    await owned.aclose()


@pytest.mark.asyncio
async def test_fallback_and_summary_models_open_their_own_permits(wire: Wire) -> None:
    primary = build("openai", guard_for(wire, "provider:root"))
    fallback = build("google", guard_for(wire, "provider:fallback"))
    summary = build("openai", guard_for(wire, "provider:summary", kind="summary"))
    calls = {"n": 0}

    def handler(request: Any, lib: ModuleType) -> Any:
        calls["n"] += 1
        return _connect_error(request, lib) if calls["n"] == 1 else _success(request, lib)

    wire.handler = handler
    answer = await primary.model.with_fallbacks([fallback.model]).ainvoke("hello")
    await summary.model.ainvoke("summarize")
    assert answer.content == "hi"
    assert wire.permit_descriptors() == ["provider:root", "provider:fallback", "provider:summary"]
    assert wire.sends == 3
    for owned in (primary, fallback, summary):
        await owned.aclose()
