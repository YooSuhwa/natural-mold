"""Exercise successful HTTP responses carrying an OpenAI-compatible SSE error on the wire."""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import openai
import pytest
from pydantic import ValidationError

from app.agent_runtime.lease_provider_retry_policy import LeaseProviderRetryPolicy
from app.agent_runtime.stream_retry_policy import gateway_stream_status
from app.config import Settings, settings
from tests.authz.lease_provider_wire import build, guard_for, install_wire
from tests.authz.test_lease_provider_retries import _openai_chunk, _sse

CONCURRENCY_LIMIT = "Concurrency limit exceeded for user, please retry later"
GATEWAY_TIMEOUT = "bad response status code 504"
SSE_HEADERS = {"content-type": "text/event-stream"}


def _error_frame(message: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": "openai_error"}}


@pytest.mark.parametrize(
    ("message", "status"),
    [
        (CONCURRENCY_LIMIT, 429),
        (GATEWAY_TIMEOUT, 504),
        ("504 Gateway Time-out", 504),
        ("bad response status code 5040", None),
        ("input contains bad response status code 504", None),
        ("access denied", None),
    ],
)
async def test_sdk_stream_error_keeps_structured_body_without_http_failure(
    message: str, status: int | None
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200, headers=SSE_HEADERS, content=_sse(_error_frame(message), done=True)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = openai.AsyncOpenAI(
            api_key="fixture",
            base_url="https://gateway.invalid/v1",
            http_client=http,
            max_retries=0,
        )
        stream = await client.chat.completions.create(
            model="fixture", messages=[{"role": "user", "content": "hi"}], stream=True
        )
        async with stream:
            with pytest.raises(openai.APIError) as caught:
                await anext(aiter(stream))

    error = caught.value
    assert type(error) is openai.APIError
    assert getattr(error, "status_code", None) is None
    assert error.body == _error_frame(message)["error"]
    assert gateway_stream_status(error) == status


@pytest.mark.parametrize("status_code", [401, 403])
async def test_http_auth_failure_is_never_a_gateway_stream_error(status_code: int) -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=_error_frame(CONCURRENCY_LIMIT))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = openai.AsyncOpenAI(
            api_key="fixture",
            base_url="https://gateway.invalid/v1",
            http_client=http,
            max_retries=0,
        )
        with pytest.raises(openai.APIStatusError) as caught:
            await client.chat.completions.create(
                model="fixture", messages=[{"role": "user", "content": "hi"}], stream=True
            )
    assert gateway_stream_status(caught.value) is None


@pytest.mark.parametrize("message", [CONCURRENCY_LIMIT, GATEWAY_TIMEOUT])
async def test_leased_sdk_recovers_once_per_call_across_100_pre_output_errors(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai"})
    wire = install_wire(monkeypatch)
    calls = 0

    def respond(_: Any, lib: ModuleType) -> Any:
        nonlocal calls
        calls += 1
        # Each request has a distinct HTTP stream. The failing first response closes
        # before the retry can supply the only visible token for that logical call.
        frame = _error_frame(message) if calls % 2 else _openai_chunk("recovered")
        return lib.Response(200, headers=SSE_HEADERS, content=_sse(frame, done=True))

    wire.handler = respond
    policy = LeaseProviderRetryPolicy(max_attempts=2, initial_delay=0)
    owned = build("openai", guard_for(wire, policy=policy))
    try:
        for _ in range(100):
            result = [chunk.content async for chunk in owned.model.astream("hi") if chunk.content]
            assert result == ["recovered"]
    finally:
        await owned.aclose()

    assert calls == wire.opened == 200
    assert [event[0] for event in wire.events] == [
        "open",
        "send",
        "close",
        "sleep",
        "open",
        "send",
        "close",
    ] * 100


@pytest.mark.parametrize(
    ("enabled", "http_status", "message"),
    [
        (False, 200, CONCURRENCY_LIMIT),
        (True, 200, "bad response status code 5040"),
        (True, 401, CONCURRENCY_LIMIT),
        (True, 403, GATEWAY_TIMEOUT),
    ],
)
async def test_leased_sdk_sends_once_for_disabled_unknown_or_denied_calls(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    enabled: bool,
    http_status: int,
    message: str,
) -> None:
    monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai"} if enabled else set())
    wire = install_wire(monkeypatch)

    def respond(_: Any, lib: ModuleType) -> Any:
        error = _error_frame(message)
        if http_status != 200:
            return lib.Response(http_status, json=error)
        return lib.Response(200, headers=SSE_HEADERS, content=_sse(error, done=True))

    wire.handler = respond
    metric_logger = "app.agent_runtime.stream_retry_policy"
    caplog.set_level(logging.INFO, logger=metric_logger)
    owned = build("openai", guard_for(wire, policy=LeaseProviderRetryPolicy(max_attempts=3)))
    try:
        with pytest.raises(openai.APIError):
            await anext(owned.model.astream("hi"))
    finally:
        await owned.aclose()
    assert [event[0] for event in wire.events] == ["open", "send", "close"]
    metrics = [entry.getMessage() for entry in caplog.records if entry.name == metric_logger]
    assert metrics == (
        ["model_stream_attempt attempt=1 output_started=False outcome=unclassified"]
        if enabled
        else []
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"code": "invalid_api_key"},
        {"status": 403},
        {"type": "permission_error"},
        {"param": "Authorization"},
    ],
)
def test_classified_body_cannot_override_contradictory_fields(extra: dict[str, object]) -> None:
    request = httpx.Request("POST", "https://gateway.invalid/v1/chat/completions")
    # Status-bearing errors have their own SDK type; these values travel in a 200 SSE frame.
    body = {"message": CONCURRENCY_LIMIT, **extra}
    assert gateway_stream_status(openai.APIError(CONCURRENCY_LIMIT, request, body=body)) is None


def test_gateway_retry_requires_explicit_known_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHAT_STREAM_RETRY_PROVIDERS", raising=False)
    assert Settings().chat_stream_retry_providers == frozenset()
    monkeypatch.setenv("CHAT_STREAM_RETRY_PROVIDERS", '["openai_compatible"]')
    assert Settings().chat_stream_retry_providers == frozenset({"openai_compatible"})
    monkeypatch.setenv("CHAT_STREAM_RETRY_PROVIDERS", '["all"]')
    with pytest.raises(ValidationError, match="chat_stream_retry_providers"):
        Settings()
