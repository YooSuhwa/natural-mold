"""Environment-variable backed secrets provider — the default fallback."""

from __future__ import annotations

import os
from typing import Any

from app.credentials.external_secrets.base import ProviderState, SecretsProvider


class EnvSecretsProvider(SecretsProvider):
    """Read secrets from process env vars with a configurable prefix.

    A reference like ``__external__: {provider: env, ref: "OPENAI"}`` resolves
    to ``os.environ["MOLDY_SECRET_OPENAI"]`` when the prefix is the default
    ``MOLDY_SECRET_``.
    """

    name = "env"
    display_name = "Environment Variables"

    def __init__(self, prefix: str = "MOLDY_SECRET_") -> None:
        super().__init__()
        self._prefix = prefix

    def init(self, settings: Any) -> None:
        prefix = getattr(settings, "external_secrets_env_prefix", None)
        if isinstance(prefix, str) and prefix:
            self._prefix = prefix
        self.state = ProviderState.INITIAL

    async def connect(self) -> None:
        # Nothing to connect — env is always available.
        self.state = ProviderState.CONNECTED

    async def disconnect(self) -> None:
        self.state = ProviderState.DISCONNECTED

    def _key(self, name: str) -> str:
        return f"{self._prefix}{name}"

    async def get_secret(self, name: str) -> str | None:
        value = os.environ.get(self._key(name))
        return value if value else None

    async def has_secret(self, name: str) -> bool:
        return bool(os.environ.get(self._key(name)))

    async def list_secrets(self) -> list[str]:
        return [
            key[len(self._prefix) :]
            for key in os.environ
            if key.startswith(self._prefix)
        ]

    async def test(self) -> tuple[bool, str | None]:
        return True, None


__all__ = ["EnvSecretsProvider"]
