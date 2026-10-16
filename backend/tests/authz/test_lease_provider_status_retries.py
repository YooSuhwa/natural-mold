"""HTTP 503 and mid-stream failures stay one send per permit (internal I12, slice I12a)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import ModuleType
from typing import Any

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
