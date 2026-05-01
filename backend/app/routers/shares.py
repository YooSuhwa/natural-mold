"""Share link router — owner-side mutations + public read-only resolution.

Two shapes:

- ``/api/conversations/{id}/share`` (auth required) — owner creates / fetches
  / revokes the share token. Idempotent on create: if an active token exists
  it is returned instead of issuing a new one, so the owner sees a stable URL.
- ``/api/shares/{token}`` and ``/api/shares/{token}/messages`` (no auth) —
  public visitor surface returning a sanitized snapshot. ``404`` is returned
  for revoked / unknown tokens to avoid leaking link existence.

Public endpoints are rate-limited per IP (``slowapi``) and back the response
with a tiny TTL snapshot cache keyed on the conversation's active branch
checkpoint — new turns naturally bust the cache without manual invalidation.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import conversation_not_found, share_not_found
from app.rate_limit import limiter
from app.schemas.conversation import MessagesEnvelope
from app.schemas.share import (
    SharedAgentBrief,
    SharedConversationView,
    ShareLinkResponse,
)
from app.services import chat_service, share_cache, share_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shares"])


# ---------------------------------------------------------------------------
# Owner endpoints
# ---------------------------------------------------------------------------


async def _require_owned_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, user: CurrentUser
):
    """Same 404-on-foreign-owner contract as ``chat_service.get_owned_conversation``."""
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()
    return conv


@router.get(
    "/api/conversations/{conversation_id}/share",
    response_model=ShareLinkResponse | None,
)
async def get_active_share(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ShareLinkResponse | None:
    """Return the active share link, or ``None`` when the conversation is private."""
    await _require_owned_conversation(db, conversation_id, user)
    link = await share_service.get_active_share_for_conversation(db, conversation_id)
    if link is None:
        return None
    return ShareLinkResponse.model_validate(link)


@router.post(
    "/api/conversations/{conversation_id}/share",
    response_model=ShareLinkResponse,
)
async def create_share(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ShareLinkResponse:
    """Make the conversation public. Returns the existing token if already shared."""
    conv = await _require_owned_conversation(db, conversation_id, user)
    link = await share_service.create_or_get_active_share(db, conv, user.id)
    return ShareLinkResponse.model_validate(link)


@router.delete(
    "/api/conversations/{conversation_id}/share",
    status_code=204,
)
async def revoke_share(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    """Revoke any active share link for the conversation. Idempotent."""
    await _require_owned_conversation(db, conversation_id, user)
    revoked_tokens = await share_service.revoke_share(db, conversation_id)
    # Drop cached snapshots so a stale (token, checkpoint) within TTL can't
    # outlive the revoke. Cheap: at most one active token per conversation.
    for token in revoked_tokens:
        share_cache.invalidate_token(token)


# ---------------------------------------------------------------------------
# Public visitor endpoints (no auth)
# ---------------------------------------------------------------------------


@router.get("/api/shares/{share_token}", response_model=SharedConversationView)
@limiter.limit(settings.share_public_rate_limit)
async def get_public_share(
    request: Request,
    share_token: str,
    db: AsyncSession = Depends(get_db),
) -> SharedConversationView:
    """Resolve a share token to a read-only conversation snapshot.

    The full message list is included so the public page renders in a single
    round-trip (vs. fetching share + messages separately). For very long
    conversations a future revision may paginate.
    """
    bundle = await share_service.load_share_with_conversation_and_agent(db, share_token)
    if bundle is None:
        raise share_not_found()
    link, conversation, agent = bundle

    checkpoint_id = conversation.active_branch_checkpoint_id
    cached = share_cache.get_snapshot(share_token, checkpoint_id)
    if cached is not None:
        return cached

    messages = await chat_service.list_messages_from_checkpointer(
        db, conversation, user_id=None
    )

    snapshot = SharedConversationView(
        share_token=link.share_token,
        conversation_title=conversation.title,
        conversation_created_at=conversation.created_at,
        agent=SharedAgentBrief(
            name=agent.name,
            description=agent.description,
            image_url=(
                f"/api/agents/{agent.id}/image?t={int(agent.updated_at.timestamp())}"
                if agent.image_path
                else None
            ),
        ),
        messages=messages,
        shared_at=link.created_at,
    )
    share_cache.put_snapshot(share_token, checkpoint_id, snapshot)
    return snapshot


@router.get("/api/shares/{share_token}/messages", response_model=MessagesEnvelope)
@limiter.limit(settings.share_public_rate_limit)
async def get_public_share_messages(
    request: Request,
    share_token: str,
    db: AsyncSession = Depends(get_db),
) -> MessagesEnvelope:
    """Public messages endpoint — useful when the consumer wants the same
    branch metadata shape as the authenticated ``/api/conversations/.../messages``
    endpoint without owning the conversation."""
    bundle = await share_service.load_share_with_conversation_and_agent(db, share_token)
    if bundle is None:
        raise share_not_found()
    _, conversation, _ = bundle

    checkpoint_id = conversation.active_branch_checkpoint_id
    # ``SharedConversationView``와 같은 token이지만 envelope은 shape이 다르므로
    # share_cache가 별도 namespace key로 캡슐화한다.
    cached = share_cache.get_envelope(share_token, checkpoint_id)
    if cached is not None:
        return cached

    messages = await chat_service.list_messages_from_checkpointer(
        db, conversation, user_id=None
    )
    envelope = MessagesEnvelope(
        messages=messages,
        active_tip_message_id=None,
        active_checkpoint_id=checkpoint_id,
    )
    share_cache.put_envelope(share_token, checkpoint_id, envelope)
    return envelope
