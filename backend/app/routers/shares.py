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
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import conversation_not_found, share_not_found
from app.models.message_event import MessageEvent
from app.rate_limit import limiter
from app.schemas.artifact import ArtifactSummary
from app.schemas.conversation import MessageResponse, MessagesEnvelope
from app.schemas.share import (
    SharedAgentBrief,
    SharedConversationView,
    ShareLinkResponse,
)
from app.services import (
    artifact_service,
    audit_service,
    chat_service,
    share_cache,
    share_service,
    trace_storage,
)
from app.services.artifact_service import (
    ArtifactNotFoundError,
    is_text_preview_artifact,
)

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


def _public_artifact_summary(summary: ArtifactSummary, share_token: str) -> ArtifactSummary:
    base_url = f"/api/shares/{share_token}/artifacts/{summary.id}"
    return summary.model_copy(
        update={
            "url": base_url,
            "preview_url": f"{base_url}/content",
            "download_url": f"{base_url}/download",
        }
    )


async def _load_public_share_messages(
    db: AsyncSession,
    *,
    share_token: str,
    conversation,
) -> list[MessageResponse]:
    checkpoint_id = conversation.active_branch_checkpoint_id
    cached_envelope = share_cache.get_envelope(share_token, checkpoint_id)
    if cached_envelope is not None:
        return cached_envelope.messages
    cached_snapshot = share_cache.get_snapshot(share_token, checkpoint_id)
    if cached_snapshot is not None:
        return cached_snapshot.messages
    return await chat_service.list_messages_from_checkpointer(db, conversation, user_id=None)


def _is_trace_visible_in_share(
    trace: MessageEvent,
    *,
    conversation_id: uuid.UUID,
    visible_message_ids: set[str],
) -> bool:
    linked_message_ids = {str(message_id) for message_id in trace.linked_message_ids or []}
    if linked_message_ids:
        return not linked_message_ids.isdisjoint(visible_message_ids)

    candidate_message_ids = {trace.assistant_msg_id}
    candidate_message_ids.add(str(parse_msg_id(trace.assistant_msg_id, conversation_id, 0)))
    return not candidate_message_ids.isdisjoint(visible_message_ids)


def _filter_public_share_traces(
    traces: list[MessageEvent],
    *,
    conversation_id: uuid.UUID,
    messages: list[MessageResponse],
) -> list[MessageEvent]:
    visible_message_ids = {str(message.id) for message in messages}
    if not visible_message_ids:
        return []
    return [
        trace
        for trace in traces
        if _is_trace_visible_in_share(
            trace,
            conversation_id=conversation_id,
            visible_message_ids=visible_message_ids,
        )
    ]


async def _list_public_share_visible_artifacts(
    db: AsyncSession,
    *,
    share_token: str,
    user_id: uuid.UUID,
    conversation,
) -> list[ArtifactSummary]:
    messages = await _load_public_share_messages(
        db,
        share_token=share_token,
        conversation=conversation,
    )
    visible_message_ids = [str(message.id) for message in messages]
    if not visible_message_ids:
        return []

    artifacts_by_message_id = await artifact_service.list_conversation_artifacts_by_message_id(
        db,
        user_id=user_id,
        conversation_id=conversation.id,
    )
    visible_artifacts: list[ArtifactSummary] = []
    seen_artifact_ids: set[str] = set()
    for message_id in visible_message_ids:
        for artifact in artifacts_by_message_id.get(message_id, []):
            artifact_id = str(artifact.id)
            if artifact_id in seen_artifact_ids:
                continue
            seen_artifact_ids.add(artifact_id)
            visible_artifacts.append(artifact)
    return visible_artifacts


async def _get_public_share_visible_artifact(
    db: AsyncSession,
    *,
    share_token: str,
    user_id: uuid.UUID,
    conversation,
    artifact_id: uuid.UUID,
) -> ArtifactSummary:
    visible_artifacts = await _list_public_share_visible_artifacts(
        db,
        share_token=share_token,
        user_id=user_id,
        conversation=conversation,
    )
    for artifact in visible_artifacts:
        if artifact.id == artifact_id:
            return artifact
    raise ArtifactNotFoundError("artifact not visible in shared snapshot")


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
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ShareLinkResponse:
    """Make the conversation public. Returns the existing token if already shared."""
    conv = await _require_owned_conversation(db, conversation_id, user)
    link = await share_service.create_or_get_active_share(db, conv, user.id)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="conversation.share_create",
        target_type="conversation",
        target_id=conversation_id,
        target_name_snapshot=conv.title,
        target_owner_user_id=user.id,
        outcome="success",
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
async def revoke_share(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> None:
    """Revoke any active share link for the conversation. Idempotent."""
    conv = await _require_owned_conversation(db, conversation_id, user)
    revoked_tokens = await share_service.revoke_share(db, conversation_id)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="conversation.share_revoke",
        target_type="conversation",
        target_id=conversation_id,
        target_name_snapshot=conv.title,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={"revoked_count": len(revoked_tokens)},
    )
    await db.commit()
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
    # W6: turn별 SSE event trace를 함께 노출 → 공개 페이지에서 도구/Skill
    # 칩 렌더용. trace가 없는(W5 이전에 만든) 대화는 빈 list로 응답.
    traces = _filter_public_share_traces(
        await trace_storage.get_traces_for_conversation(db, conversation.id),
        conversation_id=conversation.id,
        messages=messages,
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
        traces=traces,  # type: ignore[arg-type]  # ORM rows → Pydantic via from_attributes
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


@router.get("/api/shares/{share_token}/artifacts", response_model=list[ArtifactSummary])
@limiter.limit(settings.share_public_rate_limit)
async def list_public_share_artifacts(
    request: Request,
    share_token: str,
    db: AsyncSession = Depends(get_db),
) -> list[ArtifactSummary]:
    bundle = await share_service.load_share_with_conversation_and_agent(db, share_token)
    if bundle is None:
        raise share_not_found()
    _, conversation, agent = bundle
    artifacts = await _list_public_share_visible_artifacts(
        db,
        share_token=share_token,
        user_id=agent.user_id,
        conversation=conversation,
    )
    return [_public_artifact_summary(artifact, share_token) for artifact in artifacts]


@router.get("/api/shares/{share_token}/artifacts/{artifact_id}", response_model=ArtifactSummary)
@limiter.limit(settings.share_public_rate_limit)
async def get_public_share_artifact(
    request: Request,
    share_token: str,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ArtifactSummary:
    bundle = await share_service.load_share_with_conversation_and_agent(db, share_token)
    if bundle is None:
        raise share_not_found()
    _, conversation, agent = bundle
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
@limiter.limit(settings.share_public_rate_limit)
async def get_public_share_artifact_content(
    request: Request,
    share_token: str,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    bundle = await share_service.load_share_with_conversation_and_agent(db, share_token)
    if bundle is None:
        raise share_not_found()
    _, conversation, agent = bundle
    try:
        summary = await _get_public_share_visible_artifact(
            db,
            share_token=share_token,
            user_id=agent.user_id,
            conversation=conversation,
            artifact_id=artifact_id,
        )
        if is_text_preview_artifact(summary):
            return await artifact_service.read_artifact_text_content(
                db,
                user_id=agent.user_id,
                artifact_id=artifact_id,
                conversation_id=conversation.id,
            )
        _artifact, _version, path = await artifact_service.get_artifact_download_path(
            db,
            user_id=agent.user_id,
            artifact_id=artifact_id,
            conversation_id=conversation.id,
        )
        return FileResponse(
            path,
            media_type=summary.mime_type,
            headers={"Cache-Control": "public, max-age=300"},
        )
    except ArtifactNotFoundError as exc:
        raise share_not_found() from exc


@router.get("/api/shares/{share_token}/artifacts/{artifact_id}/download")
@limiter.limit(settings.share_public_rate_limit)
async def download_public_share_artifact(
    request: Request,
    share_token: str,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    bundle = await share_service.load_share_with_conversation_and_agent(db, share_token)
    if bundle is None:
        raise share_not_found()
    _, conversation, agent = bundle
    try:
        summary = await _get_public_share_visible_artifact(
            db,
            share_token=share_token,
            user_id=agent.user_id,
            conversation=conversation,
            artifact_id=artifact_id,
        )
        artifact, _version, path = await artifact_service.get_artifact_download_path(
            db,
            user_id=agent.user_id,
            artifact_id=summary.id,
            conversation_id=conversation.id,
        )
        return FileResponse(
            path,
            filename=artifact.display_name,
            media_type=artifact.mime_type,
            headers={"Cache-Control": "public, max-age=300"},
        )
    except ArtifactNotFoundError as exc:
        raise share_not_found() from exc
