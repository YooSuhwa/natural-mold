"""Two real loopback HTTP gateways sharing one provider family have independent retry authority."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import httpx
import openai
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer as LoopbackServer
from langgraph.checkpoint.memory import InMemorySaver

from app.agent_runtime import lease_owned_model_client, model_provider_builders
from app.agent_runtime.lease_model_factory import LeaseModelSpec, create_lease_chat_model
from app.agent_runtime.lease_provider_retry_policy import LeaseProviderRetryPolicy
from app.agent_runtime.runtime_component_builder import _prepare_agent
from app.agent_runtime.stream_retry_policy import stream_error_retryable
from app.config import settings
from tests.agent_runtime.test_direct_stream_retry import _config, _messages, _text
from tests.authz.lease_provider_wire import Wire, guard_for
from tests.authz.test_lease_provider_retries import _openai_chunk, _sse
from tests.conftest import TestSession

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)

type RunPath = Literal["direct", "prepared"]
CONCURRENCY = "Concurrency limit exceeded for user, please retry later"
TIMEOUT = "bad response status code 504"


@dataclass
class Gateway:
    origin: str = ""
    message: str = TIMEOUT
    first_status: int = 200
    redirect_to: str | None = None
    models: list[str] = field(default_factory=list)

    async def completion(self, request: web.Request) -> web.Response:
        payload = await request.json()
        assert payload["stream"] is True
        self.models.append(payload["model"])
        if self.redirect_to:
            raise web.HTTPTemporaryRedirect(f"{self.redirect_to}/v1/chat/completions")
        if len(self.models) == 1:
            if self.first_status != 200:
                return web.json_response(
                    {"error": {"message": "gateway busy"}}, status=self.first_status
                )
            frame = {"error": {"message": self.message, "type": "openai_error"}}
        else:
            frame = _openai_chunk("recovered")
        return web.Response(body=_sse(frame, done=True), content_type="text/event-stream")


@asynccontextmanager
async def _serve() -> AsyncIterator[Gateway]:
    gateway = Gateway()
    app = web.Application()
    app.router.add_post("/v1/chat/completions", gateway.completion)
    async with LoopbackServer(app, host="127.0.0.1") as server:
        gateway.origin = str(server.make_url("/").origin())
        yield gateway


@pytest.fixture
async def gateways(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Gateway, Gateway]]:
    async with _serve() as a, _serve() as b:
        permitted = {a.origin, b.origin}
        request_impl = httpx.AsyncHTTPTransport.handle_async_request

        async def loopback_only(
            transport: httpx.AsyncHTTPTransport, request: httpx.Request
        ) -> httpx.Response:
            assert f"{request.url.scheme}://{request.url.netloc.decode()}" in permitted
            return await request_impl(transport, request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", loopback_only)
        monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai_compatible"})
        monkeypatch.setattr(
            settings, "chat_stream_retry_origins", {"openai_compatible": [a.origin]}
        )
        monkeypatch.setattr("app.agent_runtime.runtime.usage_guard.async_session", TestSession)
        monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", InMemorySaver)
        monkeypatch.setattr(model_provider_builders, "_MODEL_CACHE", OrderedDict())
        monkeypatch.setenv("OPENAI_BASE_URL", f"{a.origin}/v1")
        # Honor explicit base URLs and keep CI proxy configuration out of both servers.
        # Opt into redirect following so the SDK's final request origin can differ from
        # the model's effective base origin. Lease production clients do not follow redirects.
        with httpx.Client(trust_env=False) as sync_client:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as async_client:
                monkeypatch.setattr(
                    model_provider_builders, "outbound_http_client", lambda **_: async_client
                )
                monkeypatch.setattr(
                    model_provider_builders, "outbound_sync_http_client", lambda **_: sync_client
                )
                monkeypatch.setattr(
                    lease_owned_model_client,
                    "new_owned_async_client",
                    lambda lib: lib.AsyncClient(trust_env=False, follow_redirects=True),
                )
                yield a, b


async def _consume(path: RunPath, gateway: Gateway, *, fallback: Gateway | None = None) -> str:
    if path == "direct":
        cfg = _config(explicit_retries=2)
        cfg.base_url = f"{gateway.origin}/v1"
        cfg.model_fallback_chain = (
            [
                {
                    "provider": "openai_compatible",
                    "model_name": "fallback-model",
                    "base_url": f"{fallback.origin}/v1",
                }
            ]
            if fallback is not None
            else []
        )
        agent, history, config = await _prepare_agent(
            cfg, messages_history=[{"role": "user", "content": "Hi"}]
        )
        return "".join(_text([frame async for frame in _messages(agent, history, config)]))

    guard = guard_for(Wire(), policy=LeaseProviderRetryPolicy(max_attempts=3, initial_delay=0))
    spec = LeaseModelSpec("openai_compatible", "gpt-4o-mini", "fixture", f"{gateway.origin}/v1")
    owned = create_lease_chat_model(spec, guard=guard)
    try:
        return "".join([chunk.text async for chunk in owned.model.astream("Hi")])
    finally:
        await owned.aclose()


@pytest.mark.parametrize("path", ["direct", "prepared"])
@pytest.mark.parametrize("message", [CONCURRENCY, TIMEOUT])
async def test_only_verified_origin_retries_same_family_and_same_error(
    gateways: tuple[Gateway, Gateway], path: RunPath, message: str
) -> None:
    a, b = gateways
    a.message = b.message = message
    assert await _consume(path, a) == "recovered"
    with pytest.raises(openai.APIError) as failed:
        await _consume(path, b)
    assert str(failed.value.request.url) == f"{b.origin}/v1/chat/completions"
    assert a.models == ["gpt-4o-mini"] * 2
    assert b.models == ["gpt-4o-mini"]


@pytest.mark.parametrize("path", ["direct", "prepared"])
@pytest.mark.parametrize("reverse", [False, True])
async def test_cross_origin_redirect_never_qualifies_a_stream_error_for_replay(
    gateways: tuple[Gateway, Gateway], path: RunPath, reverse: bool
) -> None:
    a, b = gateways
    source, target = (b, a) if reverse else (a, b)
    source.redirect_to = target.origin
    with pytest.raises(openai.APIError) as failed:
        await _consume(path, source)
    assert str(failed.value.request.url) == f"{target.origin}/v1/chat/completions"
    assert source.models == target.models == ["gpt-4o-mini"]


async def test_fallback_candidate_requires_its_own_verified_origin(
    gateways: tuple[Gateway, Gateway],
) -> None:
    a, b = gateways
    # The ordinary HTTP failure may select an existing fallback. The in-band
    # frame on that unverified fallback must not cause another send.
    a.first_status = 503
    with pytest.raises(openai.APIError) as failed:
        await _consume("direct", a, fallback=b)
    assert str(failed.value.request.url) == f"{b.origin}/v1/chat/completions"
    assert a.models == ["gpt-4o-mini"]
    assert b.models == ["fallback-model"]


async def test_verified_in_band_error_cannot_dispatch_to_unverified_fallback(
    gateways: tuple[Gateway, Gateway],
) -> None:
    a, b = gateways
    assert await _consume("direct", a, fallback=b) == "recovered"
    assert a.models == ["gpt-4o-mini"] * 2
    assert b.models == []


@pytest.mark.parametrize("missing", ["client", "request"])
def test_missing_origin_never_falls_back_to_other_classifier(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    origin = "http://127.0.0.1:8081"
    monkeypatch.setattr(settings, "chat_stream_retry_providers", {"openai_compatible"})
    monkeypatch.setattr(settings, "chat_stream_retry_origins", {"openai_compatible": [origin]})
    url = "/v1/chat/completions" if missing == "request" else f"{origin}/v1/chat/completions"
    exc = openai.APIError(
        TIMEOUT, httpx.Request("POST", url), body={"message": TIMEOUT, "type": "openai_error"}
    )
    assert not stream_error_retryable(
        exc,
        lambda _: True,
        provider="openai_compatible",
        client_base_url=None if missing == "client" else f"{origin}/v1",
    )
