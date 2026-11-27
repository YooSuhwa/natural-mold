from __future__ import annotations

from typing import Literal

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.authz.artifact_scope import readable_artifact_predicate
from app.authz.conversation_input_scope import lineage_input_predicate
from app.authz.conversation_scope import require_private_conversation
from app.authz.scoping import private_scope_predicate
from app.models.conversation_artifact import ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_attachment import MessageAttachment
from app.models.token_usage import TokenUsage
from app.schemas.conversation_history import (
    ConversationHistoryAttachmentResponse,
    ConversationHistoryLinks,
    ConversationHistoryResponse,
    ConversationHistoryUsageResponse,
)
from app.schemas.conversation_refs import ConversationHistoryRef
from app.schemas.conversation_run import ConversationRunHistoryResponse
from app.schemas.conversation_run_input import ConversationRunInputResponse
from app.services.artifacts.summary import _summaries_from_artifacts
from app.services.conversation_history_content import (
    ConversationHistoryContent,
    ConversationHistoryReadRequest,
    load_conversation_history_content,
    read_purged_history_content,
)
from app.services.conversation_run_service import readable_conversation_run_ids

_LIFECYCLE = TypeAdapter(Literal["active", "archived", "purged"])


async def read_conversation_history(
    request: ConversationHistoryReadRequest,
) -> ConversationHistoryResponse:
    """Read one private Conversation without requiring a live Agent row."""
    db = request.db
    conversation = await require_private_conversation(
        db,
        request.conversation_id,
        request.context,
    )
    content = await load_conversation_history_content(request, conversation)
    run_ids = await readable_conversation_run_ids(db, conversation, request.context)
    runs_query = (
        select(ConversationRun)
        .where(ConversationRun.conversation_id == conversation.id)
        .options(selectinload(ConversationRun.metrics))
        .order_by(ConversationRun.created_at.desc())
    )
    inputs_query = (
        select(ConversationRunInput)
        .where(ConversationRunInput.conversation_id == conversation.id)
        .order_by(ConversationRunInput.position, ConversationRunInput.created_at)
    )
    usage_query = (
        select(TokenUsage)
        .where(TokenUsage.conversation_id == conversation.id)
        .order_by(TokenUsage.created_at)
    )
    artifact_scope = private_scope_predicate(ConversationArtifact, request.context)
    if run_ids is not None:
        runs_query = runs_query.where(ConversationRun.id.in_(run_ids))
        inputs_query = inputs_query.where(lineage_input_predicate(conversation, run_ids))
        usage_query = usage_query.where(
            TokenUsage.run_id.in_(run_ids),
            TokenUsage.agent_id == conversation.agent_id,
            TokenUsage.caller_user_id == request.context.user_id,
            TokenUsage.org_id == request.context.org_id,
            TokenUsage.tenant_id == request.context.tenant_id,
        )
        artifact_scope = await readable_artifact_predicate(
            db, request.context, ConversationArtifact.conversation_id == conversation.id
        )
    runs = list(await db.scalars(runs_query))
    inputs = list(await db.scalars(inputs_query))
    usage = list(await db.scalars(usage_query))
    attachments = list(
        await db.scalars(
            select(MessageAttachment)
            .where(
                MessageAttachment.conversation_id == conversation.id,
                private_scope_predicate(MessageAttachment, request.context),
            )
            .order_by(MessageAttachment.created_at)
        )
    )
    artifacts = list(
        await db.scalars(
            select(ConversationArtifact)
            .where(
                ConversationArtifact.conversation_id == conversation.id,
                artifact_scope,
                ConversationArtifact.status != "deleted",
            )
            .order_by(ConversationArtifact.created_at)
        )
    )
    lifecycle = _LIFECYCLE.validate_python(conversation.agent_lifecycle_state)
    run_responses = [ConversationRunHistoryResponse.model_validate(row) for row in runs]
    prefix = f"/api/conversations/{conversation.id}"
    return ConversationHistoryResponse(
        conversation=ConversationHistoryRef(
            id=conversation.id,
            agent_id=conversation.agent_id,
            agent_name=conversation.agent_name_snapshot,
            lifecycle_state=lifecycle,
            archived_at=conversation.agent_archived_at_snapshot,
            purged_at=conversation.agent_purged_at,
            read_only=lifecycle != "active",
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        ),
        manifest=content.manifest,
        messages=content.messages,
        branches=content.branches,
        state=content.state,
        trace=content.trace,
        checkpoints=content.checkpoints,
        events=content.events,
        latest_run=run_responses[0] if run_responses else None,
        runs=run_responses,
        inputs=[ConversationRunInputResponse.model_validate(row) for row in inputs],
        usage=[ConversationHistoryUsageResponse.model_validate(row) for row in usage],
        attachments=[
            ConversationHistoryAttachmentResponse.model_validate(row) for row in attachments
        ],
        artifacts=await _summaries_from_artifacts(db, artifacts, include_names=True),
        links=ConversationHistoryLinks(
            self=f"{prefix}/history",
            messages=f"{prefix}/messages",
            state=f"{prefix}/langgraph/threads/{conversation.id}/state",
            trace=f"{prefix}/traces",
            files=f"{prefix}/files",
        ),
    )


__all__ = [
    "ConversationHistoryContent",
    "ConversationHistoryReadRequest",
    "read_conversation_history",
    "read_purged_history_content",
]
