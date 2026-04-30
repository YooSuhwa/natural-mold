"""External secrets providers (env vars, Vault, ...).

Public surface:
- :class:`SecretsProvider` ABC and :class:`ProviderState` enum
- :class:`EnvSecretsProvider`, :class:`VaultSecretsProvider`
- :class:`ExternalSecretsProxy` singleton ``proxy``
- :func:`resolve_external_refs` — walk a credential payload and replace
  ``__external__`` markers with values fetched from the configured providers.
"""

from app.credentials.external_secrets.base import ProviderState, SecretsProvider
from app.credentials.external_secrets.env_provider import EnvSecretsProvider
from app.credentials.external_secrets.proxy import (
    EXTERNAL_MARKER,
    ExternalSecretsProxy,
    proxy,
    resolve_external_refs,
)
from app.credentials.external_secrets.vault_provider import VaultSecretsProvider

__all__ = [
    "EXTERNAL_MARKER",
    "EnvSecretsProvider",
    "ExternalSecretsProxy",
    "ProviderState",
    "SecretsProvider",
    "VaultSecretsProvider",
    "proxy",
    "resolve_external_refs",
]
