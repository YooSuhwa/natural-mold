"""Immutable sanitized-history verification for transaction B of Agent purge."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
    ConversationHistoryManifest,
)
from app.schemas.conversation_history import JsonValue
from app.services.conversation_history_projection import canonical_payload_sha256


def _checkpoint_payload(row: ConversationHistoryCheckpoint) -> dict[str, JsonValue]:
    return {
        "checkpoint_id": row.checkpoint_id,
        "parent_checkpoint_id": row.parent_checkpoint_id,
        "checkpoint_namespace": row.checkpoint_namespace,
        "channel_values": row.channel_values,
        "messages": row.messages,
        "branch": row.branch,
        "trace": row.trace,
    }


def _event_payload(row: ConversationHistoryEvent) -> list[JsonValue]:
    return row.events


async def verify_history_manifest(
    db: AsyncSession,
    manifest: ConversationHistoryManifest,
) -> bool:
    """Recompute every row checksum and the canonical manifest checksum."""
    checkpoints = tuple(
        await db.scalars(
            select(ConversationHistoryCheckpoint)
            .where(ConversationHistoryCheckpoint.manifest_id == manifest.id)
            .order_by(
                ConversationHistoryCheckpoint.checkpoint_namespace,
                ConversationHistoryCheckpoint.checkpoint_id,
            )
        )
    )
    events = tuple(
        await db.scalars(
            select(ConversationHistoryEvent)
            .where(ConversationHistoryEvent.manifest_id == manifest.id)
            .order_by(
                ConversationHistoryEvent.created_at,
                ConversationHistoryEvent.assistant_message_id,
            )
        )
    )
    if len(checkpoints) != manifest.checkpoint_count:
        return False
    if sum(len(row.events) for row in events) != manifest.event_count:
        return False
    if any(
        canonical_payload_sha256(_checkpoint_payload(row)) != row.payload_sha256
        for row in checkpoints
    ):
        return False
    if any(canonical_payload_sha256(_event_payload(row)) != row.payload_sha256 for row in events):
        return False
    payload: JsonValue = {
        "schema_version": 1,
        "conversation_id": str(manifest.conversation_id),
        "source_agent_id": str(manifest.source_agent_id_snapshot),
        "checkpoints": [
            {**_checkpoint_payload(row), "payload_sha256": row.payload_sha256}
            for row in checkpoints
        ],
        "events": [
            {
                "assistant_message_id": row.assistant_message_id,
                "events": row.events,
                "payload_sha256": row.payload_sha256,
            }
            for row in events
        ],
    }
    return canonical_payload_sha256(payload) == manifest.canonical_payload_sha256


async def verified_manifests(
    db: AsyncSession,
    agent_id: UUID,
    conversation_ids: tuple[UUID, ...],
) -> tuple[ConversationHistoryManifest, ...] | None:
    manifests = tuple(
        await db.scalars(
            select(ConversationHistoryManifest)
            .where(
                ConversationHistoryManifest.conversation_id.in_(conversation_ids),
                ConversationHistoryManifest.schema_version == 1,
            )
            .order_by(ConversationHistoryManifest.conversation_id)
        )
    )
    if {row.conversation_id for row in manifests} != set(conversation_ids):
        return None
    for manifest in manifests:
        if (
            manifest.state != "complete"
            or manifest.source_agent_id_snapshot != agent_id
            or not await verify_history_manifest(db, manifest)
        ):
            return None
    return manifests


__all__ = ["verified_manifests", "verify_history_manifest"]
