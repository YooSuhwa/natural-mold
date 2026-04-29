"""HashiCorp Vault (KV v2) backed secrets provider via ``hvac``.

Token authentication only for now — sufficient for self-hosted Vault and the
common bootstrap path. Settings:

- ``settings.vault_url``     — e.g. ``https://vault.example.com:8200``
- ``settings.vault_token``   — root or scoped token
- ``settings.vault_kv_mount``— KV v2 mount path (default ``secret``)

A secret reference ``ref="openai/api_key"`` resolves to the ``api_key`` field
under the path ``openai`` in the KV v2 store.
"""

from __future__ import annotations

import logging
from typing import Any

from app.credentials.external_secrets.base import ProviderState, SecretsProvider

logger = logging.getLogger(__name__)


class VaultSecretsProvider(SecretsProvider):
    name = "vault"
    display_name = "HashiCorp Vault"

    def __init__(self) -> None:
        super().__init__()
        self._url: str = ""
        self._token: str = ""
        self._mount: str = "secret"
        self._client: Any = None

    def init(self, settings: Any) -> None:
        self._url = (getattr(settings, "vault_url", "") or "").rstrip("/")
        self._token = getattr(settings, "vault_token", "") or ""
        self._mount = (
            getattr(settings, "vault_kv_mount", "secret") or "secret"
        )
        self.state = ProviderState.INITIAL

    async def connect(self) -> None:
        if not self._url or not self._token:
            self.state = ProviderState.ERROR
            raise RuntimeError("vault provider requires vault_url and vault_token")

        try:
            import hvac  # local import — optional dep
        except ImportError as exc:  # pragma: no cover — packaged at install
            self.state = ProviderState.ERROR
            raise RuntimeError(
                "hvac is required for the Vault secrets provider"
            ) from exc

        self._client = hvac.Client(url=self._url, token=self._token)
        if not self._client.is_authenticated():
            self.state = ProviderState.ERROR
            raise RuntimeError("vault token failed authentication")
        self.state = ProviderState.CONNECTED

    async def disconnect(self) -> None:
        self._client = None
        self.state = ProviderState.DISCONNECTED

    def _split_ref(self, name: str) -> tuple[str, str | None]:
        """Split ``"path/to/secret/field"`` into ``(path, field)``.

        If the ref has a single component, treats the whole thing as the path
        and returns the entire data dict via :meth:`get_secret` as JSON-y string.
        """

        if "/" not in name:
            return name, None
        path, _, field = name.rpartition("/")
        return path, field

    async def get_secret(self, name: str) -> str | None:
        if self._client is None:
            await self.connect()
        client: Any = self._client
        path, field = self._split_ref(name)
        try:
            response = client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point=self._mount,
                raise_on_deleted_version=True,
            )
        except Exception as exc:  # noqa: BLE001 — hvac raises broad exceptions
            logger.warning("vault read failed for %s: %s", name, exc)
            return None
        data = (response or {}).get("data", {}).get("data") or {}
        if field is not None:
            value = data.get(field)
            return None if value is None else str(value)
        # Return a deterministic string representation when no field is given.
        # Callers should normally include a field component.
        if not data:
            return None
        if len(data) == 1:
            return str(next(iter(data.values())))
        return None

    async def has_secret(self, name: str) -> bool:
        return (await self.get_secret(name)) is not None

    async def list_secrets(self) -> list[str]:
        if self._client is None:
            await self.connect()
        client: Any = self._client
        try:
            response = client.secrets.kv.v2.list_secrets(
                path="", mount_point=self._mount
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("vault list failed: %s", exc)
            return []
        return list(response.get("data", {}).get("keys", []) or [])

    async def test(self) -> tuple[bool, str | None]:
        try:
            await self.connect()
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return True, None


__all__ = ["VaultSecretsProvider"]
