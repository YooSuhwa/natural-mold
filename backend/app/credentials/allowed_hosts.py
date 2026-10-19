"""Default ``credentials.allowed_hosts`` (D18, Q14; F-11).

Same rule for new credentials and the m87/F-11 backfill: the hosts the credential type
declares (``CredentialDefinition.hosts``), plus the host of each MCP server the owner
attaches it to. Generic HTTP keys stay empty and are then only sent in the owner's own
conversation runs (``app.outbound.destination``).
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.registry import registry
from app.models.credential import Credential
from app.outbound.destination import OutboundBlockedError, require_https
from app.outbound.hosts import host_of, is_ip_literal, validate_host_pattern


def default_allowed_hosts(definition_key: str) -> list[str] | None:
    definition = registry.get(definition_key)
    hosts = list(definition.hosts) if definition is not None else []
    return hosts or None


async def remember_server_host(
    db: AsyncSession, credential_id: uuid.UUID | None, url: str | None, actor: uuid.UUID
) -> None:
    """Add an MCP server's host when its editor attaches their own credential.

    Someone else's credential is never widened: its owner decides where it may go.
    """

    if credential_id is None or not url:
        return
    credential = await db.get(Credential, credential_id)
    if credential is None or credential.user_id != actor:
        return
    host = host_of(url)
    if is_ip_literal(host):  # IP literals only when the owner configures them explicitly
        return
    try:
        host = validate_host_pattern(host)
    except ValueError:  # malformed: nothing to remember
        return
    current = list(credential.allowed_hosts or [])
    if host not in current:
        credential.allowed_hosts = sorted({*current, host})


def require_credentialed_url(url: str) -> None:
    """422 for a credentialed remote MCP server that is not https (11.9)."""

    try:
        require_https(urlsplit(url).scheme.lower(), host_of(url))
    except OutboundBlockedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


__all__ = ["default_allowed_hosts", "remember_server_host", "require_credentialed_url"]
