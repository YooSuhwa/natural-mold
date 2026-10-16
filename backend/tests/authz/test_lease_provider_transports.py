"""Lease-owned provider transports send once per permit (internal I12, slice I12a)."""

from __future__ import annotations

from typing import Any, cast

import google.genai as genai_pkg
import httpx
import langchain_google_genai.chat_models as installed_google
import pytest
from google.genai.types import GenerationConfig

from app.agent_runtime import lease_google_chat_model as google_module
from app.agent_runtime import model_client_cache
from app.agent_runtime.lease_bound_model import LeaseProviderTransportError
from app.agent_runtime.lease_google_chat_model import LeaseGoogleChatModel
from app.agent_runtime.lease_model_factory import LeaseModelSpec, create_lease_chat_model
from tests.authz.lease_provider_wire import (
    CONNECTION_ERRORS,
    FORBIDDEN,
    PROVIDERS,
    Wire,
    build,
    drive,
    guard_for,
    install_wire,
)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return install_wire(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ainvoke", "astream"])
@pytest.mark.parametrize("case", sorted(PROVIDERS))
async def test_connection_failure_sends_once(wire: Wire, case: str, mode: str) -> None:
    owned = build(case, guard_for(wire))
    with pytest.raises(CONNECTION_ERRORS):
        await drive(owned, mode)
    assert wire.sends == 1
    assert [event[0] for event in wire.events] == ["open", "send", "close"]
    await owned.aclose()


@pytest.mark.asyncio
async def test_google_ainvoke_connection_failure_sends_once(wire: Wire) -> None:
    owned = build("google", guard_for(wire))
    with pytest.raises(httpx.ConnectError):
        await owned.model.ainvoke("hello")
    assert wire.sends == 1
    await owned.aclose()


@pytest.mark.asyncio
async def test_google_astream_connection_failure_sends_once(wire: Wire) -> None:
    owned = build("google", guard_for(wire))
    with pytest.raises(httpx.ConnectError):
        _ = [chunk async for chunk in owned.model.astream("hello")]
    assert wire.sends == 1
    await owned.aclose()


@pytest.mark.asyncio
async def test_google_subclass_binds_exact_owned_async_client_without_default_client(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Any] = []
    installed_calls: list[Any] = []

    class SpyClient(genai_pkg.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.append(kwargs["http_options"])
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(google_module, "Client", SpyClient)
    monkeypatch.setattr(installed_google, "Client", lambda *a, **k: installed_calls.append(k))
    owned = build("google", guard_for(wire))
    model = cast(LeaseGoogleChatModel, owned.model)
    api_client = model.client._api_client  # type: ignore[union-attr]
    injected = owned._cleanup.async_http

    assert len(built) == 1 and installed_calls == []
    assert built[0].httpx_async_client is injected
    assert api_client._async_httpx_client is injected
    assert api_client._use_aiohttp() is False
    assert model.max_retries == 1
    assert cast(Any, api_client._http_options.retry_options).attempts == 1
    assert owned.transport.client_identity == id(injected)
    assert owned.transport.sdk_attempts == 1 and owned.transport.adapter_max_retries == 1
    await owned.aclose()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("key", sorted(FORBIDDEN))
def test_google_transport_and_retry_overrides_fail_before_request_config(
    wire: Wire, key: str, nested: bool
) -> None:
    params = {"model_kwargs": {key: FORBIDDEN[key]}} if nested else {key: FORBIDDEN[key]}
    with pytest.raises(LeaseProviderTransportError) as caught:
        build("google", guard_for(wire), **params)
    assert caught.value.code == "provider_transport_not_single_send"
    assert wire.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ainvoke", "astream"])
@pytest.mark.parametrize("key", sorted(FORBIDDEN))
async def test_invocation_overrides_fail_before_permit_and_send(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, key: str, mode: str
) -> None:
    built_configs: list[Any] = []
    original = installed_google.ChatGoogleGenerativeAI._build_request_config

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        built_configs.append(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(installed_google.ChatGoogleGenerativeAI, "_build_request_config", spy)
    owned = build("google", guard_for(wire))
    with pytest.raises(LeaseProviderTransportError):
        await drive(owned, mode, **{key: FORBIDDEN[key]})
    assert wire.events == [] and built_configs == []
    await owned.aclose()


@pytest.mark.parametrize("retries", [0, 2, None])
def test_google_request_config_rejects_non_one_max_retries(wire: Wire, retries: Any) -> None:
    owned = build("google", guard_for(wire))
    with pytest.raises(LeaseProviderTransportError):
        owned.model._build_request_config(  # type: ignore[attr-defined]
            None, None, [], GenerationConfig(), None, None, max_retries=retries
        )
    assert wire.sends == 0


def test_google_model_outside_factory_and_unsupported_providers_are_rejected(
    wire: Wire,
) -> None:
    with pytest.raises(LeaseProviderTransportError):
        cast(Any, LeaseGoogleChatModel)(model="gemini-2.5-flash", api_key="k", max_retries=1)
    for provider in ("bedrock", "e2e_scripted", "azure"):
        spec = LeaseModelSpec(provider, "m", "key")
        with pytest.raises(LeaseProviderTransportError) as caught:
            create_lease_chat_model(spec, guard=guard_for(wire))
        assert caught.value.code == "provider_transport_not_single_send"
    with pytest.raises(LeaseProviderTransportError):
        build("openai", guard_for(wire), streaming=True)
    assert wire.events == []


@pytest.mark.parametrize("case", ["openai", "anthropic", "google"])
def test_synchronous_sends_are_refused(wire: Wire, case: str) -> None:
    owned = build(case, guard_for(wire))
    with pytest.raises(LeaseProviderTransportError):
        owned.model.invoke("hello")
    assert wire.events == []


@pytest.mark.asyncio
async def test_google_cleanup_closes_each_owned_side_once(wire: Wire) -> None:
    owned = build("google", guard_for(wire))
    model = cast(LeaseGoogleChatModel, owned.model)
    counts = {"sync": 0, "async": 0, "sdk_aio": 0}
    sdk = cast(Any, model.client)
    injected = owned._cleanup.async_http
    real_sync, real_async = sdk.close, injected.aclose

    def sync_close() -> None:
        counts["sync"] += 1
        real_sync()

    async def async_close() -> None:
        counts["async"] += 1
        await real_async()

    async def sdk_aio_close() -> None:
        counts["sdk_aio"] += 1

    sdk.close, injected.aclose, sdk.aio.aclose = sync_close, async_close, sdk_aio_close
    await owned.aclose()
    await owned.aclose()
    await model.aclose()
    assert counts == {"sync": 1, "async": 1, "sdk_aio": 0}
    assert injected.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["openai", "anthropic"])
async def test_owned_clients_close_once_and_never_touch_shared_cache(wire: Wire, case: str) -> None:
    before = dict(model_client_cache._MODEL_CACHE)
    first, second = build(case, guard_for(wire)), build(case, guard_for(wire))
    assert first.model is not second.model
    assert first._cleanup.async_client is not second._cleanup.async_client
    assert dict(model_client_cache._MODEL_CACHE) == before
    closes = []
    real = first._cleanup.async_client.aclose

    async def counted() -> None:
        closes.append(1)
        await real()

    first._cleanup.async_client.aclose = counted
    await first.aclose()
    await first.aclose()
    assert closes == [1] and first._cleanup.sync_client.is_closed
    assert not second._cleanup.async_client.is_closed
    await second.aclose()
