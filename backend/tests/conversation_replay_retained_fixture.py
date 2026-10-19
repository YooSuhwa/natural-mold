"""Valid retained-history seeds for legacy protocol replay compatibility tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
    ConversationHistoryManifest,
)
from app.schemas.conversation_history import JsonValue
from app.services.conversation_history_projection import canonical_payload_sha256
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID


@dataclass(frozen=True, slots=True)
class LegacyReplaySeed:
    """One persisted legacy custom event and its purged conversation identity."""

    conversation_id: uuid.UUID
    assistant_message_id: str
    event: dict[str, JsonValue]


async def seed_legacy_purged_replay(
    db: AsyncSession, seed: LegacyReplaySeed
) -> ConversationHistoryManifest:
    """Persist one complete, verifier-valid retained history for protocol replay."""
    purged_at = datetime.now(UTC)
    source_agent_id = uuid.uuid4()
    checkpoint_values: dict[str, JsonValue] = {"summary": "[REDACTED]"}
    checkpoint_messages: list[JsonValue] = [{"type": "human", "content": "[REDACTED]"}]
    checkpoint_branch: dict[str, JsonValue] = {"active": True, "parent_checkpoint_id": None}
    checkpoint_trace: dict[str, JsonValue] = {"references": []}
    checkpoint_payload: dict[str, JsonValue] = {
        "checkpoint_id": "legacy-custom-checkpoint",
        "parent_checkpoint_id": None,
        "checkpoint_namespace": "",
        "channel_values": checkpoint_values,
        "messages": checkpoint_messages,
        "branch": checkpoint_branch,
        "trace": checkpoint_trace,
    }
    checkpoint_sha256 = canonical_payload_sha256(checkpoint_payload)
    event_values: list[JsonValue] = [seed.event]
    event_sha256 = canonical_payload_sha256(event_values)
    manifest_payload: JsonValue = {
        "schema_version": 1,
        "conversation_id": str(seed.conversation_id),
        "source_agent_id": str(source_agent_id),
        "checkpoints": [{**checkpoint_payload, "payload_sha256": checkpoint_sha256}],
        "events": [
            {
                "assistant_message_id": seed.assistant_message_id,
                "events": event_values,
                "payload_sha256": event_sha256,
            }
        ],
    }
    await ensure_unit_scope(
        db, CurrentUser(id=TEST_USER_ID, email="legacy-replay@test.dev", name="Fixture")
    )
    conversation = Conversation(
        id=seed.conversation_id,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_id=None,
        agent_lifecycle_state="purged",
        agent_archived_at_snapshot=purged_at,
        agent_purged_at=purged_at,
        agent_name_snapshot="Legacy replay fixture",
        last_activity_at=purged_at,
    )
    manifest = ConversationHistoryManifest(
        conversation_id=seed.conversation_id,
        source_agent_id_snapshot=source_agent_id,
        state="complete",
        checkpoint_count=1,
        event_count=1,
        canonical_payload_sha256=canonical_payload_sha256(manifest_payload),
    )
    db.add_all([conversation, manifest])
    await db.flush()
    db.add_all(
        [
            ConversationHistoryCheckpoint(
                manifest_id=manifest.id,
                conversation_id=seed.conversation_id,
                checkpoint_id="legacy-custom-checkpoint",
                parent_checkpoint_id=None,
                checkpoint_namespace="",
                channel_values=checkpoint_values,
                messages=checkpoint_messages,
                branch=checkpoint_branch,
                trace=checkpoint_trace,
                payload_sha256=checkpoint_sha256,
            ),
            ConversationHistoryEvent(
                manifest_id=manifest.id,
                conversation_id=seed.conversation_id,
                assistant_message_id=seed.assistant_message_id,
                sequence_start=0,
                sequence_end=0,
                events=event_values,
                payload_sha256=event_sha256,
            ),
        ]
    )
    return manifest


__all__ = ["LegacyReplaySeed", "seed_legacy_purged_replay"]
