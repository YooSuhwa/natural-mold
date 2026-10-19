from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
    ConversationHistoryManifest,
)
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_metrics import ConversationRunMetrics
from app.models.message_attachment import MessageAttachment
from app.models.token_usage import TokenUsage
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID


@dataclass(frozen=True, slots=True)
class PurgedHistorySeed:
    conversation_id: UUID
    run_id: UUID
    artifact_id: UUID
    now: datetime


async def seed_purged_history(db: AsyncSession, storage: Path) -> PurgedHistorySeed:
    now = datetime.now(UTC)
    conversation_id = uuid4()
    run_id = uuid4()
    artifact_id = uuid4()
    version_id = uuid4()
    storage.joinpath("retained.txt").write_bytes(b"retained-history!")
    conversation = Conversation(
        id=conversation_id,
        agent_id=None,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot="Recorded Agent",
        agent_lifecycle_state="purged",
        agent_archived_at_snapshot=now,
        agent_purged_at=now,
        title="Retained history",
        last_activity_at=now,
    )
    manifest = ConversationHistoryManifest(
        conversation_id=conversation_id,
        source_agent_id_snapshot=uuid4(),
        state="complete",
        checkpoint_count=1,
        event_count=1,
        canonical_payload_sha256="a" * 64,
    )
    run = ConversationRun(
        id=run_id,
        conversation_id=conversation_id,
        agent_id=None,
        user_id=TEST_USER_ID,
        source="chat",
        status="completed",
        is_active=False,
        input_preview="retained input",
        completed_at=now,
    )
    artifact = ConversationArtifact(
        id=artifact_id,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_id=None,
        conversation_id=conversation_id,
        assistant_msg_id=str(run_id),
        logical_path="reports/retained.txt",
        display_name="retained.txt",
        mime_type="text/plain",
        artifact_kind="document",
        size_bytes=17,
        sha256="b" * 64,
        current_version_id=version_id,
        status="ready",
    )
    db.add_all([conversation, manifest, run, artifact])
    await db.flush()
    db.add_all(
        [
            ConversationHistoryCheckpoint(
                manifest_id=manifest.id,
                conversation_id=conversation_id,
                checkpoint_id="retained-tip",
                parent_checkpoint_id=None,
                checkpoint_namespace="",
                channel_values={"summary": "masked"},
                messages=[{"type": "human", "content": "[REDACTED]"}],
                branch={"active": True, "parent_checkpoint_id": None},
                trace={"references": [{"trace_id": "masked-trace"}]},
                payload_sha256="c" * 64,
            ),
            ConversationHistoryEvent(
                manifest_id=manifest.id,
                conversation_id=conversation_id,
                assistant_message_id=str(run_id),
                sequence_start=0,
                sequence_end=0,
                events=[{"event": "message_end", "data": {"content": "[REDACTED]"}}],
                payload_sha256="d" * 64,
            ),
            ConversationRunMetrics(
                run_id=run_id,
                terminal_state="completed",
                prompt_tokens=9,
                completion_tokens=4,
                estimated_cost=Decimal("0.12345678"),
                usage_complete=True,
            ),
            ConversationRunInput(
                conversation_id=conversation_id,
                agent_id=None,
                user_id=TEST_USER_ID,
                client_request_id="retained-input",
                source="ui",
                status="canceled",
                priority=0,
                position=1,
                revision=2,
                input_payload={"message": "retained"},
                attachment_ids=[],
            ),
            TokenUsage(
                run_id=run_id,
                conversation_id=conversation_id,
                agent_id=None,
                model_name="recorded-model",
                prompt_tokens=9,
                completion_tokens=4,
                total_tokens=13,
                estimated_cost=Decimal("0.123456"),
            ),
            MessageAttachment(
                user_id=TEST_USER_ID,
                org_id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
                conversation_id=conversation_id,
                message_id="retained-message",
                filename="input.txt",
                mime_type="text/plain",
                size_bytes=11,
                storage_path="retained/input.txt",
                url=f"/api/uploads/{uuid4()}/content",
            ),
            ArtifactVersion(
                id=version_id,
                artifact_id=artifact_id,
                version_number=1,
                storage_provider="local",
                object_key="retained.txt",
                original_filename="retained.txt",
                size_bytes=17,
                sha256="b" * 64,
            ),
        ]
    )
    await db.commit()
    return PurgedHistorySeed(conversation_id, run_id, artifact_id, now)
