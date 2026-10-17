"""Share link router — owner-side mutations + public read-only resolution.

Two shapes:

- ``/api/conversations/{id}/share`` (auth required) — owner creates / fetches
  / revokes the share token. Idempotent on create: if an active token exists
  it is returned instead of issuing a new one, so the owner sees a stable URL.
- ``/api/shares/{token}`` and ``/api/shares/{token}/messages`` (no auth) —
  public visitor surface returning a sanitized snapshot. ``404`` is returned
  for revoked / unknown tokens to avoid leaking link existence.

Public endpoints are rate-limited per ``(token company, IP)`` (``slowapi``, I-08) and
back the response
with a tiny TTL snapshot cache keyed on the conversation's active branch
checkpoint — new turns naturally bust the cache without manual invalidation.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, SHARE_TOKEN, authz_policy
from app.config import settings
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_org_context,
    owned_conversation,
    verify_csrf,
)
from app.error_codes import share_not_found
from app.models.conversation import Conversation
from app.rate_limit import limiter, share_key
from app.schemas.artifact import ArtifactSummary
from app.schemas.conversation import MessagesEnvelope
from app.schemas.share import SharedConversationView, ShareLinkResponse
from app.services import audit_service, share_cache, share_service

# ``chat_service``/``trace_storage``/``chat_secrets`` are re-exported: tests patch
# ``app.routers.shares.<module>.<function>`` and ``share_public_view`` reads them as attributes.
from app.services import chat_service as chat_service
from app.services import trace_storage as trace_storage
from app.services.artifact_service import ArtifactNotFoundError
from app.services.chat import secrets as chat_secrets  # noqa: F401
from app.services.share_public_view import (
    _get_public_share_visible_artifact,
    _list_public_share_visible_artifacts,
    _public_artifact_summary,
    build_public_envelope,
    build_public_snapshot,
    public_artifact_file,
    require_public_share,
)
from app.services.share_rate_scope import public_share_rate_scope

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shares"])


# ---------------------------------------------------------------------------
# Owner endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/conversations/{conversation_id}/share",
    response_model=ShareLinkResponse | None,
    dependencies=[Depends(owned_conversation)],
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def get_active_share(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> ShareLinkResponse | None:
    """Return the active share link, or ``None`` when the conversation is private."""
    link = await share_service.get_active_share_for_conversation(db, conversation_id, context)
    if link is None:
        return None
    return ShareLinkResponse.model_validate(link)


@router.post(
    "/api/conversations/{conversation_id}/share",
    response_model=ShareLinkResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def create_share(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    conv: Conversation = Depends(owned_conversation),
) -> ShareLinkResponse:
    """Make the conversation public. Returns the existing token if already shared."""
    link = await share_service.create_or_get_active_share(db, conv, context)
    await audit_service.record_self_event(
        db,
        user,
        action="conversation.share_create",
        target_type="conversation",
        target_id=conversation_id,
        target_name=conv.title,
        request=request,
        metadata={
            "share_id": str(link.id),
            "token_prefix": link.share_token[:8],
        },
    )
    await db.commit()
    return ShareLinkResponse.model_validate(link)


@router.delete(
    "/api/conversations/{conversation_id}/share",
    status_code=204,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def revoke_share(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    conv: Conversation = Depends(owned_conversation),
) -> None:
    """Revoke any active share link for the conversation. Idempotent."""
    revoked_tokens = await share_service.revoke_share(db, conversation_id, context)
    await audit_service.record_self_event(
        db,
        user,
        action="conversation.share_revoke",
        target_type="conversation",
        target_id=conversation_id,
        target_name=conv.title,
        request=request,
        metadata={"revoked_count": len(revoked_tokens)},
    )
    await db.commit()
    # Drop cached snapshots so a stale (token, checkpoint) within TTL can't
    # outlive the revoke. Cheap: at most one active token per conversation.
    for token in revoked_tokens:
        share_cache.invalidate_token(token)


# ---------------------------------------------------------------------------
# Public visitor endpoints (no auth). Every read resolves the token through
# ``load_share_with_conversation_and_agent`` first, which also applies the org's
# ``allow_public_share_links`` setting (D-10) and the company check (I-08: the
# link's company and organization must be active, else 404) before any cache
# lookup. ``public_share_rate_scope`` puts the token's company into the
# ``(tenant_id, ip)`` rate-limit bucket before the limiter runs.
# ---------------------------------------------------------------------------


@router.get("/api/shares/{share_token}", response_model=SharedConversationView)
@limiter.limit(settings.share_public_rate_limit, key_func=share_key)
@authz_policy(SHARE_TOKEN())
async def get_public_share(
    request: Request,
    share_token: str,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(public_share_rate_scope),
) -> SharedConversationView:
    """Resolve a share token to a read-only conversation snapshot.

    The full message list is included so the public page renders in a single
    round-trip (vs. fetching share + messages separately). For very long
    conversations a future revision may paginate.
    """
    link, conversation, agent = await require_public_share(db, share_token)

    checkpoint_id = conversation.active_branch_checkpoint_id
    cached = share_cache.get_snapshot(share_token, checkpoint_id)
    if cached is not None:
        return cached

    snapshot = await build_public_snapshot(db, link, conversation, agent)
    share_cache.put_snapshot(share_token, checkpoint_id, snapshot)
    return snapshot


@router.get("/api/shares/{share_token}/messages", response_model=MessagesEnvelope)
@limiter.limit(settings.share_public_rate_limit, key_func=share_key)
@authz_policy(SHARE_TOKEN())
async def get_public_share_messages(
    request: Request,
    share_token: str,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(public_share_rate_scope),
) -> MessagesEnvelope:
    """Public messages endpoint — useful when the consumer wants the same
    branch metadata shape as the authenticated ``/api/conversations/.../messages``
    endpoint without owning the conversation."""
    _, conversation, _ = await require_public_share(db, share_token)

    checkpoint_id = conversation.active_branch_checkpoint_id
    # ``SharedConversationView``와 같은 token이지만 envelope은 shape이 다르므로
    # share_cache가 별도 namespace key로 캡슐화한다.
    cached = share_cache.get_envelope(share_token, checkpoint_id)
    if cached is not None:
        return cached

    envelope = await build_public_envelope(db, conversation)
    share_cache.put_envelope(share_token, checkpoint_id, envelope)
    return envelope


@router.get("/api/shares/{share_token}/artifacts", response_model=list[ArtifactSummary])
@limiter.limit(settings.share_public_rate_limit, key_func=share_key)
@authz_policy(SHARE_TOKEN())
async def list_public_share_artifacts(
    request: Request,
    share_token: str,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(public_share_rate_scope),
) -> list[ArtifactSummary]:
    _, conversation, agent = await require_public_share(db, share_token)
    artifacts = await _list_public_share_visible_artifacts(
        db,
        share_token=share_token,
        user_id=agent.user_id,
        conversation=conversation,
    )
    return [_public_artifact_summary(artifact, share_token) for artifact in artifacts]


@router.get("/api/shares/{share_token}/artifacts/{artifact_id}", response_model=ArtifactSummary)
@limiter.limit(settings.share_public_rate_limit, key_func=share_key)
@authz_policy(SHARE_TOKEN())
async def get_public_share_artifact(
    request: Request,
    share_token: str,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(public_share_rate_scope),
) -> ArtifactSummary:
    _, conversation, agent = await require_public_share(db, share_token)
    try:
        summary = await _get_public_share_visible_artifact(
            db,
            share_token=share_token,
            user_id=agent.user_id,
            conversation=conversation,
            artifact_id=artifact_id,
        )
        return _public_artifact_summary(summary, share_token)
    except ArtifactNotFoundError as exc:
        raise share_not_found() from exc


@router.get("/api/shares/{share_token}/artifacts/{artifact_id}/content")
@limiter.limit(settings.share_public_rate_limit, key_func=share_key)
@authz_policy(SHARE_TOKEN())
async def get_public_share_artifact_content(
    request: Request,
    share_token: str,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(public_share_rate_scope),
):
    _, conversation, agent = await require_public_share(db, share_token)
    try:
        return await public_artifact_file(
            db, share_token, conversation, agent, artifact_id, download=False
        )
    except ArtifactNotFoundError as exc:
        raise share_not_found() from exc


@router.get("/api/shares/{share_token}/artifacts/{artifact_id}/download")
@limiter.limit(settings.share_public_rate_limit, key_func=share_key)
@authz_policy(SHARE_TOKEN())
async def download_public_share_artifact(
    request: Request,
    share_token: str,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(public_share_rate_scope),
):
    _, conversation, agent = await require_public_share(db, share_token)
    try:
        return await public_artifact_file(
            db, share_token, conversation, agent, artifact_id, download=True
        )
    except ArtifactNotFoundError as exc:
        raise share_not_found() from exc
