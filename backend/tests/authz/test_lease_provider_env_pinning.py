"""Env cannot redirect lease transports; failed construction closes owned clients (I12a).

Failure-path tests run inside a running event loop, as production construction does.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.agent_runtime import lease_owned_model_client as owned_module
from app.agent_runtime.lease_bound_model import LeaseProviderTransportError
from app.agent_runtime.lease_google_transport import redact_base_url
from app.agent_runtime.lease_model_factory import LeaseModelSpec, create_lease_chat_model
from tests.authz.lease_provider_wire import Wire, build, drive, guard_for, install_wire

CANONICAL_HOSTS = {
    "anthropic": "api.anthropic.com",
    "google": "generativelanguage.googleapis.com",
    "openai": "api.openai.com",
}
REDIRECT_ENV = {
    "ANTHROPIC_BASE_URL": "https://anthropic-redirect.invalid",
    "ANTHROPIC_API_URL": "https://anthropic-api-redirect.invalid",
    "LANGSMITH_GATEWAY": "https://gateway-redirect.invalid",
    "GOOGLE_GEMINI_BASE_URL": "https://gemini-redirect.invalid",
    "OPENAI_API_BASE": "https://openai-api-base-redirect.invalid/v1",
    "OPENAI_BASE_URL": "https://openai-base-url-redirect.invalid/v1",
}
PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return install_wire(monkeypatch)


@pytest.fixture
def created_clients(monkeypatch: pytest.MonkeyPatch, wire: Wire) -> list[Any]:
    created: list[Any] = []
    real_async, real_sync = owned_module.new_owned_async_client, owned_module.new_owned_sync_client

    def record_async(lib: Any = httpx) -> Any:
        created.append(real_async(lib))
        return created[-1]

    def record_sync(lib: Any = httpx) -> Any:
        created.append(real_sync(lib))
        return created[-1]

    monkeypatch.setattr(owned_module, "new_owned_async_client", record_async)
    monkeypatch.setattr(owned_module, "new_owned_sync_client", record_sync)
    return created


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["openai", "anthropic", "google"])
async def test_redirect_env_cannot_move_the_canonical_base_url(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    for name, value in REDIRECT_ENV.items():
        monkeypatch.setenv(name, value)
    owned = build(case, guard_for(wire))
    with pytest.raises(Exception):  # noqa: B017, PT011 — the wire refuses every connection
        await drive(owned, "ainvoke")
    assert wire.hosts == [CANONICAL_HOSTS[case]]
    assert CANONICAL_HOSTS[case] in str(owned.transport.base_url)
    await owned.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["openai", "anthropic", "google"])
async def test_env_proxies_are_ignored_by_the_explicit_transport(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    for name in PROXY_ENV:
        monkeypatch.setenv(name, "http://env-proxy.invalid:3128")
    owned = build(case, guard_for(wire))
    with pytest.raises(Exception):  # noqa: B017, PT011 — the wire refuses every connection
        await drive(owned, "ainvoke")
    assert wire.sends == 1 and wire.hosts == [CANONICAL_HOSTS[case]]
    await owned.aclose()


@pytest.mark.asyncio
async def test_openai_proxy_env_is_a_transport_rejection_and_closes_clients(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, created_clients: list[Any]
) -> None:
    monkeypatch.setenv("OPENAI_PROXY", "http://openai-proxy.invalid:3128")
    with pytest.raises(LeaseProviderTransportError) as caught:
        build("openai", guard_for(wire))
    assert caught.value.code == "provider_transport_not_single_send"
    assert created_clients and all(client.is_closed for client in created_clients)
    assert wire.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["openai", "anthropic", "google"])
async def test_construction_failure_closes_every_owned_client(
    wire: Wire, created_clients: list[Any], case: str
) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 — pydantic rejects the malformed scalar
        build(case, guard_for(wire), max_tokens="not-a-number")
    assert len(created_clients) == 2
    assert all(client.is_closed for client in created_clients)
    assert wire.events == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://user:pw@proxy.example:8443/v1?key=secret#frag", "https://proxy.example:8443/v1"),
        ("https://api.example/v1", "https://api.example/v1"),
        (None, None),
    ],
)
def test_descriptor_base_url_drops_userinfo_query_and_fragment(
    raw: str | None, expected: str | None
) -> None:
    assert redact_base_url(raw) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["custom", "anthropic"])
async def test_factory_descriptor_base_url_is_redacted(wire: Wire, provider: str) -> None:
    spec = LeaseModelSpec(
        provider, "some-model", "test-key", base_url="https://u:p@llm.example/v1?token=t"
    )
    owned = create_lease_chat_model(spec, guard=guard_for(wire))
    assert owned.transport.base_url == "https://llm.example/v1"
    assert "token" not in repr(owned.transport) and "u:p" not in repr(owned.transport)
    await owned.aclose()


@pytest.mark.asyncio
async def test_custom_openai_family_without_base_url_is_rejected_and_closed(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, created_clients: list[Any]
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-base-url-redirect.invalid/v1")
    spec = LeaseModelSpec("custom", "some-model", "test-key")
    with pytest.raises(LeaseProviderTransportError) as caught:
        create_lease_chat_model(spec, guard=guard_for(wire))
    assert caught.value.code == "provider_transport_not_single_send"
    assert created_clients and all(client.is_closed for client in created_clients)
    assert wire.events == []
