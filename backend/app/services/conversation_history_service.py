from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.dependencies import CurrentUser
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.models.conversation_artifact import ConversationArtifact
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
    ConversationHistoryManifest,
)
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_attachment import MessageAttachment
from app.models.token_usage import TokenUsage
from app.schemas.conversation_history import (
    ConversationHistoryAttachmentResponse,
    ConversationHistoryCheckpointResponse,
    ConversationHistoryEventResponse,
    ConversationHistoryLinks,
    ConversationHistoryManifestResponse,
    ConversationHistoryResponse,
    ConversationHistoryUsageResponse,
    JsonValue,
)
from app.schemas.conversation_history_protocol import HistoryRequest
from app.schemas.conversation_refs import ConversationHistoryRef
from app.schemas.conversation_run import ConversationRunHistoryResponse
from app.schemas.conversation_run_input import ConversationRunInputResponse
from app.services.artifacts.summary import _summaries_from_artifacts
from app.services.conversation_protocol_history import (
    LiveThreadHistoryRequest,
    load_live_thread_history,
)
from app.services.conversation_protocol_replay import (
    LiveProtocolEventsRequest,
    load_live_protocol_events,
)

_JSON = TypeAdapter(JsonValue)
_LIFECYCLE = TypeAdapter(Literal["active", "archived", "purged"])


@dataclass(frozen=True, slots=True)
class ConversationHistoryReadRequest:
    db: AsyncSession
    conversation_id: UUID
    user: CurrentUser
    context: OrgContext


@dataclass(frozen=True, slots=True)
class ConversationHistoryContent:
    manifest: ConversationHistoryManifestResponse | None
    messages: list[JsonValue]
    branches: list[JsonValue]
    state: dict[str, JsonValue]
    trace: dict[str, JsonValue]
    checkpoints: list[JsonValue]
    events: list[ConversationHistoryEventResponse]


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
    content = await _read_content(request, conversation)
    runs = list(
        await db.scalars(
            select(ConversationRun)
            .where(ConversationRun.conversation_id == conversation.id)
            .options(selectinload(ConversationRun.metrics))
            .order_by(ConversationRun.created_at.desc())
        )
    )
    inputs = list(
        await db.scalars(
            select(ConversationRunInput)
            .where(ConversationRunInput.conversation_id == conversation.id)
            .order_by(ConversationRunInput.position, ConversationRunInput.created_at)
        )
    )
    usage = list(
        await db.scalars(
            select(TokenUsage)
            .where(TokenUsage.conversation_id == conversation.id)
            .order_by(TokenUsage.created_at)
        )
    )
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
                private_scope_predicate(ConversationArtifact, request.context),
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


async def _read_content(
    request: ConversationHistoryReadRequest,
    conversation: Conversation,
) -> ConversationHistoryContent:
    if conversation.agent_lifecycle_state != "purged":
        raw_checkpoints = await load_live_thread_history(
            LiveThreadHistoryRequest(
                conversation,
                HistoryRequest(limit=100),
                request.context,
                request.db,
                request.user,
            )
        )
        checkpoints = [_JSON.validate_python(row) for row in raw_checkpoints]
        current = checkpoints[0] if checkpoints else {}
        current_map = current if isinstance(current, dict) else {}
        raw_values = current_map.get("values", {})
        values = raw_values if isinstance(raw_values, dict) else {}
        raw_messages = values.get("messages", [])
        messages = raw_messages if isinstance(raw_messages, list) else []
        raw_events = await load_live_protocol_events(
            LiveProtocolEventsRequest(
                request.db,
                conversation,
                request.context,
            )
        )
        events = [dict(item) for item in raw_events]
        safe_events = _JSON.validate_python(events)
        return ConversationHistoryContent(
            None,
            messages,
            checkpoints,
            {key: value for key, value in values.items() if key != "messages"},
            {"events": safe_events},
            checkpoints,
            [],
        )

    return await read_purged_history_content(request.db, conversation)


async def read_purged_history_content(
    db: AsyncSession,
    conversation: Conversation,
) -> ConversationHistoryContent:
    """Read the complete sanitized manifest without hydrating a deleted Agent."""
    manifest = await db.scalar(
        select(ConversationHistoryManifest).where(
            ConversationHistoryManifest.conversation_id == conversation.id,
            ConversationHistoryManifest.schema_version == 1,
            ConversationHistoryManifest.state == "complete",
        )
    )
    if manifest is None:
        raise ConflictError(
            "CONVERSATION_HISTORY_INCOMPLETE",
            "Purged conversation has no complete retained history manifest",
        )
    checkpoints = list(
        await db.scalars(
            select(ConversationHistoryCheckpoint)
            .where(ConversationHistoryCheckpoint.manifest_id == manifest.id)
            .order_by(
                ConversationHistoryCheckpoint.checkpoint_namespace,
                ConversationHistoryCheckpoint.checkpoint_id,
            )
        )
    )
    try:
        retained_checkpoints = [
            ConversationHistoryCheckpointResponse.model_validate(row) for row in checkpoints
        ]
    except ValidationError as exc:
        raise ConflictError(
            "CONVERSATION_HISTORY_INVALID",
            "Retained checkpoint does not match the history contract",
        ) from exc
    events = list(
        await db.scalars(
            select(ConversationHistoryEvent)
            .where(ConversationHistoryEvent.manifest_id == manifest.id)
            .order_by(
                ConversationHistoryEvent.assistant_message_id,
                ConversationHistoryEvent.sequence_start,
            )
        )
    )
    active = next((row for row in retained_checkpoints if row.branch.get("active") is True), None)
    current = active or (retained_checkpoints[-1] if retained_checkpoints else None)
    checkpoint_payloads: list[JsonValue] = [
        row.model_dump(mode="json") for row in retained_checkpoints
    ]
    return ConversationHistoryContent(
        ConversationHistoryManifestResponse.model_validate(manifest),
        list(current.messages) if current is not None else [],
        [row.branch for row in retained_checkpoints],
        dict(current.channel_values) if current is not None else {},
        dict(current.trace) if current is not None else {},
        checkpoint_payloads,
        [ConversationHistoryEventResponse.model_validate(row) for row in events],
    )
