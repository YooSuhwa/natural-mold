"""Google lease validator and pre-permit request rejections (internal I12, slice I12a)."""

from __future__ import annotations

from typing import Any, cast

import google.genai as genai_pkg
import langchain_google_genai.chat_models as installed_google
import pytest

from app.agent_runtime import lease_google_chat_model as google_module
from app.agent_runtime.lease_bound_model import LeaseProviderTransportError
from app.agent_runtime.lease_google_chat_model import LeaseGoogleChatModel
from app.agent_runtime.lease_google_transport import (
    new_google_transport_bundle,
    pending_google_transport,
)
from tests.authz.lease_provider_wire import Wire, build, drive, guard_for, install_wire

REJECTED_CONSTRUCTIONS: dict[str, dict[str, Any]] = {
    "max_retries_zero": {"max_retries": 0},
    "max_retries_three": {"max_retries": 3},
    "client_args": {"client_args": {"proxy": "http://p"}},
    "credentials": {"credentials": object()},
    "vertexai": {"vertexai": True},
    "kwargs_retries": {"model_kwargs": {"retries": 1}},
    "kwargs_max_retries": {"model_kwargs": {"max_retries": 2}},
    "kwargs_client_args": {"model_kwargs": {"client_args": {"proxy": "http://p"}}},
    "kwargs_async_client_args": {"model_kwargs": {"async_client_args": {"transport": None}}},
    "kwargs_http_options": {"model_kwargs": {"http_options": {"timeout": 5}}},
    "kwargs_retry_options": {"model_kwargs": {"retry_options": {"attempts": 5}}},
}
REQUEST_ONLY = {
    "automatic_function_calling": {"disable": False, "maximum_remote_calls": 10},
    "should_return_http_response": True,
}


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return install_wire(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(REJECTED_CONSTRUCTIONS))
async def test_validator_rejects_unprovable_configuration_under_pending_transport(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    clients: list[Any] = []
    monkeypatch.setattr(google_module, "Client", lambda *a, **k: clients.append(k))
    bundle = new_google_transport_bundle()
    options: dict[str, Any] = {
        "model": "gemini-2.5-flash",
        "api_key": "test-key",
        "max_retries": 1,
        "vertexai": False,
        **REJECTED_CONSTRUCTIONS[name],
    }
    with pending_google_transport(bundle), pytest.raises(LeaseProviderTransportError) as caught:
        cast(Any, LeaseGoogleChatModel)(**options)
    assert caught.value.code == "provider_transport_not_single_send"
    assert clients == [] and wire.events == []
    await bundle.cleanup.aclose()


@pytest.mark.asyncio
async def test_validator_accepts_the_pinned_configuration_under_pending_transport(
    wire: Wire,
) -> None:
    bundle = new_google_transport_bundle()
    options = {"model": "gemini-2.5-flash", "api_key": "k", "max_retries": 1, "vertexai": False}
    with pending_google_transport(bundle):
        model = cast(Any, LeaseGoogleChatModel)(**options)
    assert isinstance(model.client, genai_pkg.Client)
    assert model.lease_transport.client_identity == id(bundle.async_http)
    await bundle.cleanup.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ainvoke", "astream"])
@pytest.mark.parametrize("key", sorted(REQUEST_ONLY))
@pytest.mark.parametrize("case", ["openai", "anthropic", "google"])
async def test_request_only_overrides_are_rejected_before_any_permit(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, case: str, key: str, mode: str
) -> None:
    built: list[Any] = []
    original = installed_google.ChatGoogleGenerativeAI._build_request_config

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        built.append(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(installed_google.ChatGoogleGenerativeAI, "_build_request_config", spy)
    owned = build(case, guard_for(wire))
    with pytest.raises(LeaseProviderTransportError) as caught:
        await drive(owned, mode, **{key: REQUEST_ONLY[key]})
    assert caught.value.code == "provider_transport_not_single_send"
    assert wire.events == [] and wire.opened == 0 and built == []
    await owned.aclose()
