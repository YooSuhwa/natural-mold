from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_lifecycle_scope import (
    AgentLifecycleAuthorization,
    authorize_agent_lifecycle,
)
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
    ConversationHistoryManifest,
)
from app.services.agent_lifecycle_projection import lock_lifecycle_target
from app.services.agent_purge_history_validation import verified_manifests
from app.services.conversation_history_materializer import (
    HistoryProjectionRequest,
    HistorySourceIncompleteError,
    build_history_projection,
)
from app.services.conversation_history_projection import (
    UnsupportedHistoryValueError,
)
from app.services.conversation_history_secrets import (
    HistorySecretSourceError,
    collect_strict_history_secrets,
)
from app.services.conversation_history_types import (
    ConversationHistoryMaterializationRequest,
)


class HistorySnapshotIncompleteError(ConflictError):
    """The complete sanitized history could not be proven."""

    def __init__(self, reason: str) -> None:
        super().__init__("AGENT_HISTORY_SNAPSHOT_INCOMPLETE", reason)


async def _locked_snapshot_state(
    request: ConversationHistoryMaterializationRequest,
) -> tuple[Conversation, ConversationHistoryManifest | None]:
    db = request.db
    await lock_lifecycle_target(db, request.agent_id)
    authority = await authorize_agent_lifecycle(
        db,
        request.actor,
        AgentLifecycleAuthorization(
            request.agent_id,
            "purge",
            request.confirmation_name,
        ),
    )
    if authority.agent_id != request.agent_id:
        raise HistorySnapshotIncompleteError("fresh lifecycle authority mismatch")
    conversation = await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == request.conversation_id,
            Conversation.agent_id == request.agent_id,
        )
        .execution_options(populate_existing=True)
    )
    if conversation is None:
        raise HistorySnapshotIncompleteError("source conversation is unavailable")
    existing = await db.scalar(
        select(ConversationHistoryManifest)
        .where(
            ConversationHistoryManifest.conversation_id == conversation.id,
            ConversationHistoryManifest.schema_version == 1,
        )
        .execution_options(populate_existing=True)
    )
    return conversation, existing


async def _reuse_complete_manifest(
    request: ConversationHistoryMaterializationRequest,
    conversation: Conversation,
    existing: ConversationHistoryManifest | None,
) -> ConversationHistoryManifest | None:
    if existing is None or existing.state != "complete":
        return None
    verified = await verified_manifests(request.db, request.agent_id, (conversation.id,))
    if verified is None:
        raise HistorySnapshotIncompleteError("history manifest verification failed")
    return verified[0]


async def materialize_conversation_history(
    request: ConversationHistoryMaterializationRequest,
) -> ConversationHistoryManifest:
    """Fresh-authorize and atomically retain one complete sanitized history."""
    db = request.db
    authority = await authorize_agent_lifecycle(
        db,
        request.actor,
        AgentLifecycleAuthorization(
            request.agent_id,
            "purge",
            request.confirmation_name,
        ),
    )
    if authority.agent_id != request.agent_id:
        raise HistorySnapshotIncompleteError("fresh lifecycle authority mismatch")
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == request.conversation_id,
            Conversation.agent_id == request.agent_id,
        )
    )
    if conversation is None:
        raise HistorySnapshotIncompleteError("source conversation is unavailable")
    existing = await db.scalar(
        select(ConversationHistoryManifest).where(
            ConversationHistoryManifest.conversation_id == conversation.id,
            ConversationHistoryManifest.schema_version == 1,
        )
    )
    if existing is not None and existing.state == "complete":
        conversation, existing = await _locked_snapshot_state(request)
        reusable = await _reuse_complete_manifest(request, conversation, existing)
        if reusable is None:
            raise HistorySnapshotIncompleteError("history manifest changed during verification")
        return reusable

    try:
        secrets = await collect_strict_history_secrets(
            db,
            request.agent_id,
            request.conversation_id,
        )
        projection = await build_history_projection(
            HistoryProjectionRequest(request, conversation, secrets)
        )
    except (
        HistorySecretSourceError,
        HistorySourceIncompleteError,
        UnsupportedHistoryValueError,
    ) as error:
        raise HistorySnapshotIncompleteError("history sanitization failed") from error

    conversation, existing = await _locked_snapshot_state(request)
    reusable = await _reuse_complete_manifest(request, conversation, existing)
    if reusable is not None:
        return reusable

    async with db.begin_nested():
        if existing is not None:
            await db.delete(existing)
            await db.flush()
        manifest = ConversationHistoryManifest(
            conversation_id=conversation.id,
            source_agent_id_snapshot=request.agent_id,
            state="building",
            checkpoint_count=len(projection.checkpoints),
            event_count=sum(len(item.events) for item in projection.events),
            canonical_payload_sha256=projection.canonical_payload_sha256,
        )
        db.add(manifest)
        await db.flush()
        db.add_all(
            [
                ConversationHistoryCheckpoint(
                    manifest_id=manifest.id,
                    conversation_id=conversation.id,
                    checkpoint_id=item.checkpoint_id,
                    parent_checkpoint_id=item.parent_checkpoint_id,
                    checkpoint_namespace=item.checkpoint_namespace,
                    channel_values=item.channel_values,
                    messages=item.messages,
                    branch=item.branch,
                    trace=item.trace,
                    payload_sha256=item.payload_sha256,
                )
                for item in projection.checkpoints
            ]
        )
        db.add_all(
            [
                ConversationHistoryEvent(
                    manifest_id=manifest.id,
                    conversation_id=conversation.id,
                    assistant_message_id=item.assistant_message_id,
                    sequence_start=0,
                    sequence_end=max(0, len(item.events) - 1),
                    events=item.events,
                    payload_sha256=item.payload_sha256,
                )
                for item in projection.events
            ]
        )
        await db.flush()
        checkpoint_count = await db.scalar(
            select(func.count())
            .select_from(ConversationHistoryCheckpoint)
            .where(ConversationHistoryCheckpoint.manifest_id == manifest.id)
        )
        persisted_events = await db.scalars(
            select(ConversationHistoryEvent).where(
                ConversationHistoryEvent.manifest_id == manifest.id
            )
        )
        event_count = sum(len(row.events) for row in persisted_events)
        if checkpoint_count != len(projection.checkpoints) or event_count != manifest.event_count:
            raise HistorySnapshotIncompleteError("history row count mismatch")
        manifest.state = "complete"
        await db.flush()
    return manifest


async def require_complete_agent_history(
    db: AsyncSession,
    agent_id: UUID,
    conversation_ids: tuple[UUID, ...],
) -> tuple[ConversationHistoryManifest, ...]:
    """Revalidate exact manifest membership, row counts, and checksums before purge."""
    manifests = await verified_manifests(db, agent_id, conversation_ids)
    if manifests is None:
        raise HistorySnapshotIncompleteError("history manifest membership mismatch")
    return manifests


__all__ = [
    "ConversationHistoryMaterializationRequest",
    "HistorySnapshotIncompleteError",
    "materialize_conversation_history",
    "require_complete_agent_history",
]
