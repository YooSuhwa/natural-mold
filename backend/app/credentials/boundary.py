"""The only place credential ciphertext is opened outside the storage layer (11.3; F-10).

Every caller names why it opens a credential (``DecryptPurpose``); the storage layer
(``credentials/service.py``, ``tenant_encryption.py``, ``security/``) keeps its own
primitives. ``tests/authz/test_f10_decrypt_boundary.py`` fails when any other module
calls a decryption primitive directly.

Purposes and who judges them (11.3 table):

* ``run`` — agent runs, through ``run_resolution`` (mode, binding and ``can_use``
  decided there).
* ``redaction`` — read-only resolution for response masking; makes no decision.
* ``refresh`` — OAuth refresh/authorization (``mcp/auth``, ``mcp/oauth``,
  ``oauth_service``); the caller already holds the row it may use.
* ``probe`` — tests, health checks and model discovery after the requester's
  ``credential#can_use``/``can_edit`` gate.
* ``system`` — operator credentials for service flows only.
* ``install`` — marketplace credential requirement checks at install time.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from app.credentials import service as credential_service
from app.models.credential import Credential

DecryptPurpose = Literal["run", "redaction", "refresh", "probe", "system", "install"]
CredentialMode = Literal["shared", "per_user", "system"]


class SecretValues:
    """Decrypted values; never printed. Hand them to ``run_secrets``, never persist."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None) -> None:
        self._data = None if data is None else dict(data)

    def reveal(self) -> dict[str, Any] | None:
        return None if self._data is None else dict(self._data)

    def __repr__(self) -> str:
        return "SecretValues(<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True)
class ResolvedCredential:
    """One run's resolution of a tool/MCP server credential (11.3)."""

    credential_id: uuid.UUID
    mode: CredentialMode
    resource_type: str
    resource_id: uuid.UUID
    resource_owner_id: uuid.UUID | None
    credential_subject_id: uuid.UUID | None  # the account holder
    allowed_hosts: tuple[str, ...]
    values: SecretValues = field(repr=False)


async def open_values(credential: Credential, purpose: DecryptPurpose) -> dict[str, Any]:
    """Decrypt ``credential`` (external references resolved) for ``purpose``."""
    del purpose  # recorded by the call site; the static check pins who may call
    return await credential_service.decrypt_with_external(credential)


__all__ = [
    "CredentialMode",
    "DecryptPurpose",
    "ResolvedCredential",
    "SecretValues",
    "open_values",
]
