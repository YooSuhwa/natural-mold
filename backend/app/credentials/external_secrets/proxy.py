"""External secrets proxy + ``__external__`` marker resolution.

Credential payloads can embed a marker in place of a literal value:

.. code-block:: json

    {"api_key": {"__external__": {"provider": "vault", "ref": "openai/api_key"}}}

At runtime, :func:`resolve_external_refs` walks the payload and replaces every
marker with the value returned by the named provider. Markers that fail to
resolve are left as-is so the caller can surface a clear error.
"""

from __future__ import annotations

import logging
from typing import Any

from app.credentials.external_secrets.base import SecretsProvider

logger = logging.getLogger(__name__)

EXTERNAL_MARKER = "__external__"


class ExternalSecretsProxy:
    """Process-wide registry of :class:`SecretsProvider` instances."""

    def __init__(self) -> None:
        self._providers: dict[str, SecretsProvider] = {}

    def register(self, provider: SecretsProvider) -> None:
        if not provider.name:
            raise ValueError("provider must declare a non-empty .name")
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> SecretsProvider | None:
        return self._providers.get(name)

    def all(self) -> list[SecretsProvider]:
        return list(self._providers.values())

    async def get(self, provider_name: str, secret_name: str) -> str | None:
        provider = self._providers.get(provider_name)
        if provider is None:
            logger.warning("external secrets: unknown provider %r", provider_name)
            return None
        return await provider.get_secret(secret_name)

    def clear(self) -> None:
        self._providers.clear()


# Process singleton.
proxy = ExternalSecretsProxy()


async def resolve_external_refs(
    data: dict[str, Any],
    *,
    proxy: ExternalSecretsProxy = proxy,
) -> dict[str, Any]:
    """Return a copy of ``data`` with ``__external__`` markers replaced.

    Walks dicts and lists. Markers that fail to resolve are left in place; the
    caller decides whether that should fail loudly.
    """

    return await _walk(data, proxy)


async def _walk(value: Any, proxy: ExternalSecretsProxy) -> Any:
    if isinstance(value, dict):
        marker = value.get(EXTERNAL_MARKER)
        if isinstance(marker, dict):
            provider_name = marker.get("provider")
            ref = marker.get("ref")
            if isinstance(provider_name, str) and isinstance(ref, str):
                resolved = await proxy.get(provider_name, ref)
                if resolved is not None:
                    return resolved
                return value  # leave marker so caller surfaces the failure
        return {k: await _walk(v, proxy) for k, v in value.items()}
    if isinstance(value, list):
        return [await _walk(v, proxy) for v in value]
    return value


__all__ = [
    "EXTERNAL_MARKER",
    "ExternalSecretsProxy",
    "proxy",
    "resolve_external_refs",
]
