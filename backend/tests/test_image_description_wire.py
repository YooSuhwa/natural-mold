"""OpenAI-compatible auxiliary vision request and bounded response contracts."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass

import httpx
import pytest

from app.outbound.client import outbound_http_client
from app.services.chat import image_description_wire as wire


@dataclass(frozen=True)
class Request:
    endpoint: str = "https://vision.example.test/api/v1/chat/completions"
    model_name: str = "vision-model"
    timeout_seconds: float = 1.0

    async def send(self) -> tuple[str, int, int]:
        return await wire.send_image_description(
            endpoint=self.endpoint,
            api_key="dummy-test-token",
            model_name=self.model_name,
            image=b"synthetic image bytes",
            mime_type="image/png",
            question="어떤 색인가요?",
            max_output_tokens=50,
            timeout_seconds=self.timeout_seconds,
        )


def respond_with(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
) -> None:
    transport = httpx.MockTransport(handler)

    def make_client(
        *, timeout: float, https_only: bool, ssrf_guard: bool, follow_redirects: bool
    ) -> httpx.AsyncClient:
        assert https_only and ssrf_guard and not follow_redirects
        return outbound_http_client(timeout=timeout, transport=transport, https_only=https_only)

    monkeypatch.setattr(wire, "outbound_http_client", make_client)


def completion(content: str, prompt: object = 5, output: object = 2) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": output},
    }


@pytest.mark.parametrize(
    ("model", "token_field"), [("vision", "max_tokens"), ("gpt-5", "max_completion_tokens")]
)
async def test_single_approved_request(
    monkeypatch: pytest.MonkeyPatch, model: str, token_field: str
) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=completion(" 파란색입니다. "))

    respond_with(monkeypatch, handler)
    config = Request(model_name=model)
    assert await config.send() == ("파란색입니다.", 5, 2)
    assert len(sent) == 1
    request = sent[0]
    assert str(request.url) == config.endpoint
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer dummy-test-token"
    payload = json.loads(request.content)
    assert set(payload) == {"model", token_field, "messages"}
    assert payload["model"] == model and payload[token_field] == 50
    instruction, message = payload["messages"]
    assert instruction["role"] == "system"
    assert message["role"] == "user"
    question, image = message["content"]
    assert question == {"type": "text", "text": "어떤 색인가요?"}
    media_type, data = image["image_url"]["url"].split(",")
    assert media_type == "data:image/png;base64"
    assert base64.b64decode(data) == b"synthetic image bytes"


@pytest.mark.parametrize(
    "reply",
    [
        {},
        completion(" "),
        completion("secret" * 2000),
        completion("\x00"),
        completion("sensitive", prompt=True),
        completion("sensitive", prompt=-12),
        completion("sensitive", output=51),
    ],
)
async def test_malformed_or_over_budget_response(
    monkeypatch: pytest.MonkeyPatch, reply: dict[str, object]
) -> None:
    respond_with(monkeypatch, lambda _: httpx.Response(200, json=reply))
    with pytest.raises(wire.ImageDescriptionWireError, match="^provider_failed$") as raised:
        await Request().send()
    assert raised.value.__context__ is None


@pytest.mark.parametrize("status", [307, 401, 429, 503])
async def test_redirect_and_provider_errors_send_once(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(
            status,
            headers={"Location": "https://other.example.test/private"},
            text="private provider detail",
        )

    respond_with(monkeypatch, handler)
    with pytest.raises(wire.ImageDescriptionWireError, match="^provider_failed$") as raised:
        await Request().send()
    assert raised.value.__context__ is None
    assert sent == [Request().endpoint]


async def test_invalid_endpoint_never_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        pytest.fail("sent to invalid destination")

    respond_with(monkeypatch, unexpected)
    for url in (
        "http://vision.example.test/chat",
        "https://key@vision.example.test/chat",
        "https://vision.example.test/chat?key=private",
        "https://vision.example.test/chat#fragment",
    ):
        with pytest.raises(wire.ImageDescriptionWireError, match="^provider_failed$"):
            await Request(endpoint=url).send()


async def test_response_byte_limit_without_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    class Large(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for _ in range(5):
                yield b"x" * 16_384
            pytest.fail("continued reading oversized response")

    respond_with(monkeypatch, lambda _: httpx.Response(200, stream=Large()))
    with pytest.raises(wire.ImageDescriptionWireError, match="^provider_failed$"):
        await Request().send()


async def test_deadline_covers_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = asyncio.Event()

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"{"
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            closed.set()

    respond_with(monkeypatch, lambda _: httpx.Response(200, stream=Slow()))
    with pytest.raises(wire.ImageDescriptionWireError, match="^provider_timeout$"):
        await Request(timeout_seconds=0.05).send()
    assert closed.is_set()


async def test_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200)

    respond_with(monkeypatch, handler)
    task = asyncio.create_task(Request().send())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
