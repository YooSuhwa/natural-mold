"""MCP OAuth2 helpers — thin wrapper around the credential OAuth2 base.

MCP servers that require OAuth2 use the ``mcp_oauth2`` credential definition.
The token refresh + access_token injection is handled by the credential domain;
this module exists primarily as a binding point for the discovery / test code
to call when an ``auth_needed`` status is observed.
"""

from __future__ import annotations

from typing import Any

from app.credentials import service as credential_service
from app.credentials.oauth2_base import is_token_expired, refresh_oauth_token
from app.credentials.registry import registry
from app.models.credential import Credential


async def ensure_fresh_token(credential: Credential) -> dict[str, Any]:
    """Decrypt + refresh (if expired) the bound credential.

    Returns the decrypted, possibly refreshed, payload. The caller is
    responsible for persisting any updates back to the row.
    """

    data = await credential_service.decrypt_with_external(credential.data_encrypted)
    if not is_token_expired(data):
        return data

    definition = registry.require(credential.definition_key)
    refreshed = await refresh_oauth_token(definition, data)
    return refreshed


__all__ = ["ensure_fresh_token"]
