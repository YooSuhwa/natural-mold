"""Authenticated keys select their persisted organization, never a browser preference."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_api_scope import private_api_key_predicate
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.exceptions import AppError
from app.models.agent_api import AgentApiKey


def validate_api_key_state(key: AgentApiKey) -> None:
    if key.revoked_at is not None:
        raise AppError("AGENT_API_KEY_REVOKED", "Agent API key is revoked", 401)
    if key.expires_at is not None and key.expires_at <= datetime.now(UTC).replace(tzinfo=None):
        raise AppError("AGENT_API_KEY_EXPIRED", "Agent API key is expired", 401)


async def resolve_api_key_actor(db: AsyncSession, key: AgentApiKey) -> OrgContext:
    validate_api_key_state(key)
    try:
        context = await resolve_private_actor(
            db, PrivateResourceActor(key.user_id, key.org_id, key.tenant_id)
        )
    except AppError:
        raise AppError("AGENT_API_KEY_INVALID", "Agent API key is invalid", 401) from None
    candidate = await db.scalar(
        select(AgentApiKey.id).where(AgentApiKey.id == key.id, private_api_key_predicate(context))
    )
    if candidate is None:
        raise AppError("AGENT_API_KEY_INVALID", "Agent API key is invalid", 401)
    return context
