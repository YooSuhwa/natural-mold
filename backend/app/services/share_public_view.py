"""Public share read helpers: visible artifacts and traces of a shared snapshot.

Moved out of ``routers/shares.py`` (I-08 file-size split); bodies unchanged. The
helpers look ``chat_service``/``chat_secrets``/``trace_storage`` up as module
attributes, and the router re-exports those modules, so tests patching
``app.routers.shares.<module>.<function>`` still apply.
"""

from __future__ import annotations

import uuid

from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.authz.conversation_scope import resolve_conversation_actor
from app.error_codes import share_not_found
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.models.share_link import ShareLink
from app.schemas.artifact import ArtifactSummary, ArtifactTextContent
from app.schemas.conversation import MessageResponse, MessagesEnvelope, TurnTraceResponse
from app.schemas.share import SharedAgentBrief, SharedConversationView
from app.services import artifact_service, chat_service, share_cache, share_service, trace_storage
from app.services.agent_image_paths import build_agent_image_url
from app.services.artifact_service import ArtifactNotFoundError, is_text_preview_artifact
from app.services.chat import secrets as chat_secrets

PublicShare = tuple[ShareLink, Conversation, Agent]


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
    return await chat_service.list_messages_from_checkpointer(
        db,
        conversation,
        user_id=None,
        context=await resolve_conversation_actor(db, conversation.id, conversation.user_id),
    )


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


def _public_share_trace(
    trace: MessageEvent,
    *,
    secret_values: tuple[str, ...] | None = None,
) -> TurnTraceResponse:
    """Copy a persisted trace into its anonymous-public representation."""
    return TurnTraceResponse(
        assistant_msg_id=trace.assistant_msg_id,
        events=project_and_redact_protocol_data(
            "share_traces",
            trace.events or [],
            secret_values=secret_values,
        ),
        last_event_id=trace.last_event_id,
        linked_message_ids=trace.linked_message_ids,
        created_at=trace.created_at,
        completed_at=trace.completed_at,
    )


def _filter_public_share_traces(
    traces: list[MessageEvent],
    *,
    conversation_id: uuid.UUID,
    messages: list[MessageResponse],
    secret_values: tuple[str, ...] | None = None,
) -> list[TurnTraceResponse]:
    visible_message_ids = {str(message.id) for message in messages}
    if not visible_message_ids:
        return []
    return [
        _public_share_trace(trace, secret_values=secret_values)
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
        context=await resolve_conversation_actor(db, conversation.id, conversation.user_id),
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


def shared_conversation_view(
    link: ShareLink,
    conversation: Conversation,
    agent: Agent,
    messages: list[MessageResponse],
    traces: list[TurnTraceResponse],
) -> SharedConversationView:
    return SharedConversationView(
        share_token=link.share_token,
        conversation_title=conversation.title,
        conversation_created_at=conversation.created_at,
        agent=SharedAgentBrief(
            name=agent.name,
            description=agent.description,
            image_url=build_agent_image_url(
                agent.id,
                updated_at=agent.updated_at,
                image_path=agent.image_path,
            ),
        ),
        messages=messages,
        traces=traces,
        shared_at=link.created_at,
    )


async def public_artifact_file(
    db: AsyncSession,
    share_token: str,
    conversation: Conversation,
    agent: Agent,
    artifact_id: uuid.UUID,
    *,
    download: bool,
) -> ArtifactTextContent | FileResponse:
    """Body of a shared artifact: text preview / inline file, or the download."""
    summary = await _get_public_share_visible_artifact(
        db,
        share_token=share_token,
        user_id=agent.user_id,
        conversation=conversation,
        artifact_id=artifact_id,
    )
    context = await resolve_conversation_actor(db, conversation.id, conversation.user_id)
    if not download and is_text_preview_artifact(summary):
        return await artifact_service.read_artifact_text_content(
            db, context=context, artifact_id=artifact_id, conversation_id=conversation.id
        )
    artifact, _version, path = await artifact_service.get_artifact_download_path(
        db, context=context, artifact_id=summary.id, conversation_id=conversation.id
    )
    if not download:
        return FileResponse(
            path,
            media_type=summary.mime_type,
            headers={"Cache-Control": "public, max-age=300"},
        )
    return FileResponse(
        path,
        filename=artifact.display_name,
        media_type=artifact.mime_type,
        headers={"Cache-Control": "public, max-age=300"},
    )


async def require_public_share(db: AsyncSession, share_token: str) -> PublicShare:
    bundle = await share_service.load_share_with_conversation_and_agent(db, share_token)
    if bundle is None:
        raise share_not_found()
    return bundle


async def build_public_snapshot(
    db: AsyncSession, link: ShareLink, conversation: Conversation, agent: Agent
) -> SharedConversationView:
    context = await resolve_conversation_actor(db, conversation.id, conversation.user_id)
    messages = await chat_service.list_messages_from_checkpointer(
        db, conversation, user_id=None, context=context
    )
    secrets = tuple(
        await chat_secrets.collect_conversation_secret_values(db, conversation, context)
    )
    # W6: turn별 SSE event trace를 함께 노출 → 공개 페이지에서 도구/Skill
    # 칩 렌더용. trace가 없는(W5 이전에 만든) 대화는 빈 list로 응답.
    traces = _filter_public_share_traces(
        await trace_storage.get_traces_for_conversation(db, conversation.id),
        conversation_id=conversation.id,
        messages=messages,
        secret_values=secrets,
    )
    return shared_conversation_view(link, conversation, agent, messages, traces)


async def build_public_envelope(db: AsyncSession, conversation: Conversation) -> MessagesEnvelope:
    messages = await chat_service.list_messages_from_checkpointer(
        db,
        conversation,
        user_id=None,
        context=await resolve_conversation_actor(db, conversation.id, conversation.user_id),
    )
    return MessagesEnvelope(
        messages=messages,
        active_tip_message_id=None,
        active_checkpoint_id=conversation.active_branch_checkpoint_id,
    )
