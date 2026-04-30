"""Tests for external secret providers and ``__external__`` marker resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.credentials.external_secrets import (
    EnvSecretsProvider,
    ExternalSecretsProxy,
    VaultSecretsProvider,
    resolve_external_refs,
)

# -- EnvSecretsProvider ------------------------------------------------------


@pytest.fixture
def env_provider(monkeypatch: pytest.MonkeyPatch) -> EnvSecretsProvider:
    monkeypatch.setenv("MOLDY_SECRET_OPENAI", "env-openai-value")
    monkeypatch.setenv("MOLDY_SECRET_NAVER", "env-naver-value")
    provider = EnvSecretsProvider()
    return provider


@pytest.mark.asyncio
async def test_env_provider_get_secret(env_provider: EnvSecretsProvider) -> None:
    assert await env_provider.get_secret("OPENAI") == "env-openai-value"
    assert await env_provider.get_secret("MISSING") is None


@pytest.mark.asyncio
async def test_env_provider_has_secret(env_provider: EnvSecretsProvider) -> None:
    assert await env_provider.has_secret("OPENAI") is True
    assert await env_provider.has_secret("MISSING") is False


@pytest.mark.asyncio
async def test_env_provider_list(env_provider: EnvSecretsProvider) -> None:
    listed = await env_provider.list_secrets()
    assert "OPENAI" in listed
    assert "NAVER" in listed


@pytest.mark.asyncio
async def test_env_provider_test_always_ok(env_provider: EnvSecretsProvider) -> None:
    ok, err = await env_provider.test()
    assert ok is True
    assert err is None


@pytest.mark.asyncio
async def test_env_provider_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOM_FOO", "value")
    provider = EnvSecretsProvider(prefix="CUSTOM_")
    assert await provider.get_secret("FOO") == "value"


# -- VaultSecretsProvider ----------------------------------------------------


@pytest.fixture
def settings_for_vault() -> object:
    """A bare object that exposes the settings ``VaultSecretsProvider`` reads."""

    class _S:
        vault_url = "http://vault.local:8200"
        vault_token = "root"
        vault_kv_mount = "secret"

    return _S()


@pytest.mark.asyncio
async def test_vault_provider_init_and_connect(
    settings_for_vault: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = MagicMock()
    fake_client.is_authenticated.return_value = True

    fake_hvac = MagicMock()
    fake_hvac.Client.return_value = fake_client

    monkeypatch.setitem(__import__("sys").modules, "hvac", fake_hvac)

    provider = VaultSecretsProvider()
    provider.init(settings_for_vault)
    await provider.connect()
    fake_hvac.Client.assert_called_once_with(
        url="http://vault.local:8200", token="root"
    )


@pytest.mark.asyncio
async def test_vault_provider_get_secret_with_field(
    settings_for_vault: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = MagicMock()
    fake_client.is_authenticated.return_value = True
    fake_client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"api_key": "vault-secret-value"}}
    }

    fake_hvac = MagicMock()
    fake_hvac.Client.return_value = fake_client
    monkeypatch.setitem(__import__("sys").modules, "hvac", fake_hvac)

    provider = VaultSecretsProvider()
    provider.init(settings_for_vault)
    value = await provider.get_secret("openai/api_key")
    assert value == "vault-secret-value"
    fake_client.secrets.kv.v2.read_secret_version.assert_called_with(
        path="openai", mount_point="secret", raise_on_deleted_version=True
    )


@pytest.mark.asyncio
async def test_vault_provider_missing_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _S:
        vault_url = ""
        vault_token = ""
        vault_kv_mount = "secret"

    provider = VaultSecretsProvider()
    provider.init(_S())
    with pytest.raises(RuntimeError):
        await provider.connect()


# -- ExternalSecretsProxy + resolve_external_refs ----------------------------


@pytest.mark.asyncio
async def test_proxy_register_and_get(
    env_provider: EnvSecretsProvider,
) -> None:
    proxy = ExternalSecretsProxy()
    proxy.register(env_provider)
    assert await proxy.get("env", "OPENAI") == "env-openai-value"
    assert await proxy.get("env", "MISSING") is None
    assert await proxy.get("nonexistent", "OPENAI") is None


@pytest.mark.asyncio
async def test_resolve_external_refs_replaces_marker(
    env_provider: EnvSecretsProvider,
) -> None:
    proxy = ExternalSecretsProxy()
    proxy.register(env_provider)
    payload = {
        "api_key": {"__external__": {"provider": "env", "ref": "OPENAI"}},
        "name": "static",
    }
    resolved = await resolve_external_refs(payload, proxy=proxy)
    assert resolved == {"api_key": "env-openai-value", "name": "static"}


@pytest.mark.asyncio
async def test_resolve_external_refs_leaves_unresolved(
    env_provider: EnvSecretsProvider,
) -> None:
    proxy = ExternalSecretsProxy()
    proxy.register(env_provider)
    payload = {
        "api_key": {"__external__": {"provider": "env", "ref": "MISSING"}},
    }
    resolved = await resolve_external_refs(payload, proxy=proxy)
    # Unresolved markers are left in place so the caller surfaces a clear error.
    assert resolved == payload


@pytest.mark.asyncio
async def test_resolve_external_refs_walks_nested(
    env_provider: EnvSecretsProvider,
) -> None:
    proxy = ExternalSecretsProxy()
    proxy.register(env_provider)
    payload = {
        "outer": [
            {"__external__": {"provider": "env", "ref": "OPENAI"}},
            {"plain": "value"},
        ]
    }
    resolved = await resolve_external_refs(payload, proxy=proxy)
    assert resolved["outer"][0] == "env-openai-value"
    assert resolved["outer"][1] == {"plain": "value"}
