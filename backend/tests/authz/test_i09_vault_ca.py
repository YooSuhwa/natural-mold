"""I-09 (15.2.1 Moldy → OpenBao): ``VAULT_CA_CERT`` reaches the hvac client."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.credentials.external_secrets.vault_provider import VaultSecretsProvider


class _FakeClient:
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        _FakeClient.calls.append(kwargs)

    def is_authenticated(self) -> bool:
        return True


@pytest.mark.parametrize(("ca", "verify"), [("/certs/ca.crt", "/certs/ca.crt"), ("", True)])
async def test_vault_client_verifies_with_private_ca(
    monkeypatch: pytest.MonkeyPatch, ca: str, verify: object
) -> None:
    monkeypatch.setitem(sys.modules, "hvac", types.SimpleNamespace(Client=_FakeClient))
    _FakeClient.calls.clear()
    provider = VaultSecretsProvider()
    provider.init(
        types.SimpleNamespace(
            vault_url="https://vault.internal:8200",
            vault_token="t",  # noqa: S106 — dummy token for a fake client
            vault_kv_mount="secret",
            vault_ca_cert=ca,
        )
    )
    await provider.connect()
    assert _FakeClient.calls[-1]["verify"] == verify
