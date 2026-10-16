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

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import (
    AgentExecutionTarget,
    assert_agent_tree_executable,
    load_agent_tree_for_execution,
)
from app.authz.conversation_scope import (
    load_private_conversation,
    require_private_conversation,
    resolve_conversation_actor,
)
from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate, private_scoped
from app.error_codes import agent_not_found, public_share_links_disabled
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.organization import Organization
from app.models.share_link import ShareLink
from app.services.agent_lifecycle_locks import read_agent_tree

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
    db: AsyncSession, conversation_id: uuid.UUID, context: OrgContext
) -> ShareLink | None:
    await require_private_actor(db, context)
    result = await db.execute(
        private_scoped(select(ShareLink), ShareLink, context)
        .where(ShareLink.conversation_id == conversation_id)
        .where(
            select(Conversation.id)
            .where(
                Conversation.id == ShareLink.conversation_id,
                private_scope_predicate(Conversation, context),
            )
            .exists()
        )
        .execution_options(populate_existing=True)
        .where(ShareLink.revoked_at.is_(None))
        .order_by(ShareLink.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def public_links_allowed(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """The organization's ``allow_public_share_links`` setting (default on)."""
    settings = await db.scalar(
        select(Organization.settings)
        .where(Organization.id == org_id)
        .execution_options(autoflush=False)
    )
    return (settings or {}).get("allow_public_share_links") is not False


async def create_or_get_active_share(
    db: AsyncSession, conversation: Conversation, context: OrgContext
) -> ShareLink:
    conversation = await require_private_conversation(db, conversation.id, context)
    if not await public_links_allowed(db, context.org_id):
        raise public_share_links_disabled()
    if conversation.agent_id is None:
        raise agent_not_found()
    await load_agent_tree_for_execution(db, AgentExecutionTarget(conversation.agent_id, context))
    existing = await get_active_share_for_conversation(db, conversation.id, context)
    if existing is not None:
        return existing

    link = ShareLink(
        share_token=_new_token(),
        conversation_id=conversation.id,
        created_by=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
    )
    db.add(link)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent POST raced with us — partial unique index
        # ``uq_share_links_active_per_conversation`` (m30) blocks the second
        # active row. Roll back, return the winning row.
        await db.rollback()
        winner = await get_active_share_for_conversation(db, conversation.id, context)
        if winner is None:
            # Vanishingly unlikely (race + immediate revoke); re-raise.
            raise
        return winner
    await db.refresh(link)
    return link


async def revoke_share(
    db: AsyncSession, conversation_id: uuid.UUID, context: OrgContext
) -> list[str]:
    """Soft-delete every active share link for the conversation.

    Returns the list of tokens that were just revoked so the caller can drop
    them from any auth-free snapshot cache. Empty list means the conversation
    had no active share to begin with (idempotent).
    """
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    if await load_private_conversation(db, conversation_id, context) is None:
        return []
    token_result = await db.execute(
        private_scoped(select(ShareLink.share_token), ShareLink, context)
        .where(ShareLink.conversation_id == conversation_id)
        .where(ShareLink.revoked_at.is_(None))
    )
    tokens = [token for (token,) in token_result.all()]
    if not tokens:
        return []

    await db.execute(
        update(ShareLink)
        .where(
            ShareLink.org_id == context.org_id,
            ShareLink.tenant_id == context.tenant_id,
            ShareLink.created_by == context.user_id,
        )
        .where(ShareLink.conversation_id == conversation_id)
        .where(ShareLink.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC).replace(tzinfo=None))
    )
    await db.commit()
    return tokens


async def get_share_by_token(db: AsyncSession, token: str) -> ShareLink | None:
    """Resolve a token to its share row, only when still active."""
    result = await db.execute(
        select(ShareLink)
        .execution_options(populate_existing=True, autoflush=False)
        .where(ShareLink.share_token == token)
        .where(ShareLink.revoked_at.is_(None))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_share_with_conversation_and_agent(
    db: AsyncSession, token: str
) -> tuple[ShareLink, Conversation, Agent] | None:
    """Single-pass fetch for the public view: share → conversation → agent."""
    with db.no_autoflush:
        result = await db.execute(
            select(ShareLink)
            .execution_options(populate_existing=True, autoflush=False)
            .where(ShareLink.share_token == token)
            .where(ShareLink.revoked_at.is_(None))
            .limit(1)
        )
        link = result.scalar_one_or_none()
        # Checked on every public read before any snapshot cache, so turning the
        # setting off takes effect immediately without keying the cache on it.
        if link is None or not await public_links_allowed(db, link.org_id):
            return None

        conv_result = await db.execute(
            select(Conversation)
            .execution_options(populate_existing=True, autoflush=False)
            .where(Conversation.id == link.conversation_id)
        )
        conversation = conv_result.scalar_one_or_none()
        if conversation is None or conversation.agent_id is None:
            return None
        if (link.created_by, link.org_id, link.tenant_id) != (
            conversation.user_id,
            conversation.org_id,
            conversation.tenant_id,
        ):
            return None
        try:
            await resolve_conversation_actor(db, conversation.id, conversation.user_id)
            owner_context = OrgContext(
                org_id=conversation.org_id,
                tenant_id=conversation.tenant_id,
                user_id=conversation.user_id,
            )
            snapshot = await read_agent_tree(db, conversation.agent_id)
            assert_agent_tree_executable(snapshot, owner_context)
        except AppError:
            return None
        agent = await db.scalar(
            select(Agent)
            .where(
                Agent.id == conversation.agent_id,
                private_scope_predicate(Agent, owner_context),
            )
            .execution_options(populate_existing=True, autoflush=False)
        )
        if agent is None:
            return None
        return link, conversation, agent
