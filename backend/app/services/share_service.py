"""Share service — owner-side create/revoke + public-side resolve.

A "share" is one row in ``share_links`` carrying a URL-safe ``share_token``.
The owner endpoint is idempotent: calling create twice on the same
conversation returns the existing active row instead of issuing a new token.
Revocation is a soft delete (``revoked_at``) so subsequent visits 404 while
the row remains for audit. Public resolution joins through to the owning
conversation + agent so the read-only page can render the agent identity
without leaking owner / debug fields.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.share_link import ShareLink

__all__ = [
    "create_or_get_active_share",
    "get_active_share_for_conversation",
    "get_share_by_token",
    "revoke_share",
]


def _new_token() -> str:
    """22-char URL-safe token (16 bytes of entropy)."""
    return secrets.token_urlsafe(16)


async def get_active_share_for_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> ShareLink | None:
    result = await db.execute(
        select(ShareLink)
        .where(ShareLink.conversation_id == conversation_id)
        .where(ShareLink.revoked_at.is_(None))
        .order_by(ShareLink.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_or_get_active_share(
    db: AsyncSession, conversation: Conversation, user_id: uuid.UUID
) -> ShareLink:
    existing = await get_active_share_for_conversation(db, conversation.id)
    if existing is not None:
        return existing

    link = ShareLink(
        share_token=_new_token(),
        conversation_id=conversation.id,
        created_by=user_id,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


async def revoke_share(db: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """Mark every active share link for the conversation as revoked.

    Returns ``True`` when at least one row was revoked, ``False`` when there
    was no active link (router decides whether to 404 or treat as no-op).
    """
    active = await get_active_share_for_conversation(db, conversation_id)
    if active is None:
        return False
    active.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    return True


async def get_share_by_token(db: AsyncSession, token: str) -> ShareLink | None:
    """Resolve a token to its share row, only when still active."""
    result = await db.execute(
        select(ShareLink)
        .where(ShareLink.share_token == token)
        .where(ShareLink.revoked_at.is_(None))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_share_with_conversation_and_agent(
    db: AsyncSession, token: str
) -> tuple[ShareLink, Conversation, Agent] | None:
    """Single-pass fetch for the public view: share → conversation → agent."""
    result = await db.execute(
        select(ShareLink)
        .where(ShareLink.share_token == token)
        .where(ShareLink.revoked_at.is_(None))
        .limit(1)
    )
    link = result.scalar_one_or_none()
    if link is None:
        return None

    conv_result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.agent))
        .where(Conversation.id == link.conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if conversation is None or conversation.agent is None:
        return None
    return link, conversation, conversation.agent
