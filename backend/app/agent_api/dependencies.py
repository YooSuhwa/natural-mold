from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.security import parse_api_key, verify_secret
from app.agent_api.service import utc_now_naive
from app.dependencies import get_db
from app.exceptions import AppError, ForbiddenError
from app.models.agent_api import AgentApiKey, AgentApiKeyDeployment, AgentDeployment


@dataclass(frozen=True)
class ApiKeyPrincipal:
    key: AgentApiKey
    user_id: uuid.UUID

    @property
    def key_id(self) -> uuid.UUID:
        return self.key.id

    def require_scope(self, scope: str) -> None:
        if scope not in set(self.key.scopes or []):
            raise ForbiddenError(
                "AGENT_API_SCOPE_REQUIRED",
                f"API key requires '{scope}' scope",
            )


def _extract_api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    header = request.headers.get("x-api-key") or request.headers.get("X-Api-Key")
    if header:
        return header.strip() or None
    return None


def _key_options():
    return selectinload(AgentApiKey.deployment_links).selectinload(
        AgentApiKeyDeployment.deployment
    ).selectinload(AgentDeployment.agent)


async def get_api_key_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiKeyPrincipal:
    raw = _extract_api_key(request)
    if not raw:
        raise AppError(
            code="AGENT_API_KEY_REQUIRED",
            message="Agent API key is required",
            status=401,
        )
    parsed = parse_api_key(raw)
    if parsed is None:
        raise AppError(
            code="AGENT_API_KEY_INVALID",
            message="Agent API key is invalid",
            status=401,
        )
    key_id, secret = parsed
    result = await db.execute(
        select(AgentApiKey)
        .where(AgentApiKey.key_id == key_id)
        .options(_key_options())
    )
    key = result.scalar_one_or_none()
    if key is None or not verify_secret(key_id, secret, key.key_hash):
        raise AppError(
            code="AGENT_API_KEY_INVALID",
            message="Agent API key is invalid",
            status=401,
        )
    now = utc_now_naive()
    if key.revoked_at is not None:
        raise AppError(
            code="AGENT_API_KEY_REVOKED",
            message="Agent API key is revoked",
            status=401,
        )
    if key.expires_at is not None and key.expires_at <= now:
        raise AppError(
            code="AGENT_API_KEY_EXPIRED",
            message="Agent API key is expired",
            status=401,
        )
    key.last_used_at = now
    key.usage_count += 1
    await db.commit()
    return ApiKeyPrincipal(key=key, user_id=key.user_id)
