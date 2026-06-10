"""Credential resolution for MCP connections."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.interpolation import resolve_deep
from app.credentials.oauth2_base import is_token_expired, refresh_oauth_token
from app.credentials.registry import registry
from app.models.credential import Credential


@dataclass(frozen=True)
class ResolvedMcpAuth:
    credentials: dict[str, Any] | None
    headers: dict[str, str]
    error: str | None = None
    status: str | None = None


def _definition_headers(definition_key: str, credentials: dict[str, Any]) -> dict[str, str]:
    definition = registry.get(definition_key)
    if definition is None or definition.authenticate is None:
        return {}
    raw_headers = definition.authenticate.properties.get("headers", {})
    if not isinstance(raw_headers, dict):
        return {}
    resolved = resolve_deep(raw_headers, credentials)
    return {str(k): str(v) for k, v in resolved.items() if v is not None}


def _resolve_static_headers(
    static_headers: dict[str, Any] | None,
    credentials: dict[str, Any],
) -> dict[str, str]:
    if not static_headers:
        return {}
    resolved = resolve_deep(dict(static_headers), credentials)
    return {str(k): str(v) for k, v in resolved.items() if v is not None}


async def resolve_mcp_auth(
    db: AsyncSession,
    *,
    credential_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    static_headers: dict[str, Any] | None = None,
) -> ResolvedMcpAuth:
    if credential_id is None:
        return ResolvedMcpAuth(
            credentials=None,
            headers=_resolve_static_headers(static_headers, {}),
        )

    stmt = select(Credential).where(Credential.id == credential_id)
    if user_id is not None:
        stmt = stmt.where(Credential.user_id == user_id)
    credential = (await db.execute(stmt.with_for_update())).scalar_one_or_none()
    if credential is None:
        return ResolvedMcpAuth(
            credentials=None,
            headers={},
            error="credential not found",
            status="credential_not_found",
        )

    data = await credential_service.decrypt_with_external(credential.data_encrypted)
    definition = registry.get(credential.definition_key)
    if (
        definition is not None
        and definition.pre_authentication is not None
        and credential.definition_key.endswith("oauth2")
        and is_token_expired(data)
    ):
        try:
            data = await refresh_oauth_token(definition, data)
        except Exception as exc:
            credential.status = "auth_needed"
            await credential_service.write_audit_log(
                db,
                credential_id=credential.id,
                actor_user_id=credential.user_id,
                action="refresh",
                source="runtime",
                error=str(exc),
                metadata={"reason": "mcp_auth_resolver"},
            )
            await db.flush()
            return ResolvedMcpAuth(
                credentials=None,
                headers={},
                error=str(exc),
                status="auth_needed",
            )
        blob, key_id, field_keys = credential_service.encrypt_data(data)
        credential.data_encrypted = blob
        credential.key_id = key_id
        credential.field_keys = field_keys
        credential.status = "active"
        await credential_service.write_audit_log(
            db,
            credential_id=credential.id,
            actor_user_id=credential.user_id,
            action="refresh",
            source="runtime",
            metadata={"reason": "mcp_auth_resolver"},
        )
        await db.flush()

    headers = _resolve_static_headers(static_headers, data)
    headers.update(_definition_headers(credential.definition_key, data))
    return ResolvedMcpAuth(credentials=data, headers=headers)


__all__ = ["ResolvedMcpAuth", "resolve_mcp_auth"]
