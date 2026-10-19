from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from anyio import Path as AsyncPath
from sqlalchemy import delete, func, select

from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.authz.conversation_scope import require_private_conversation
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_attachment import MessageAttachment
from app.models.message_event import MessageEvent
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from app.services import conversation_run_service
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from tests.authz.agent_lifecycle_fixture import RegisteredLifecycleFixture

_DUMMY_SECRET = "dummy-b08-secret-for-redaction"


@dataclass(frozen=True, slots=True)
class RevokedOwnerProof:
    revoked_ack: bool
    inactive_ack: bool
    remote_ack_absent: bool
    input_revision: int
    terminal_events: int
    audit_events: int
    retained_attachment: bytes
    retained_artifact: bytes
    retained_history_masked: bool
    revoked_denial: tuple[int, str]


async def _seed_retained_owner_rows(fixture: RegisteredLifecycleFixture) -> tuple[UUID, UUID, UUID]:
    graph = fixture.graph
    actors = fixture.actors
    attachment_id, artifact_id, version_id, event_id = (uuid4() for _ in range(4))
    sanitized = redact_protocol_data(
        "message_end", {"content": _DUMMY_SECRET}, secret_values=(_DUMMY_SECRET,)
    )
    artifact_bytes = graph.artifact_version_paths[-1].read_bytes()
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    async with fixture.sessions() as db:
        conversation = await db.get(Conversation, graph.conversation_ids[1])
        assert conversation is not None
        conversation.agent_id, conversation.agent_lifecycle_state = None, "purged"
        conversation.agent_purged_at = datetime.now(UTC)
        db.add(
            MessageEvent(
                id=event_id,
                conversation_id=conversation.id,
                assistant_msg_id=f"retained-{event_id}",
                events=[{"id": str(event_id), "event": "message_end", "data": sanitized}],
                last_event_id=str(event_id),
                status="completed",
            )
        )
        db.add(
            MessageAttachment(
                id=attachment_id,
                tenant_id=actors.tenant_id,
                org_id=actors.org_id,
                user_id=actors.owner_two_id,
                conversation_id=conversation.id,
                message_id="retained-owner-message",
                filename="retained.bin",
                mime_type="application/octet-stream",
                size_bytes=graph.attachment_path.stat().st_size,
                storage_path=str(graph.attachment_path),
                url="/fixture/retained.bin",
            )
        )
        artifact = ConversationArtifact(
            id=artifact_id,
            tenant_id=actors.tenant_id,
            org_id=actors.org_id,
            user_id=actors.owner_two_id,
            agent_id=None,
            conversation_id=conversation.id,
            assistant_msg_id=f"retained-{event_id}",
            logical_path="artifacts/retained.bin",
            display_name="retained.bin",
            extension="bin",
            mime_type="application/octet-stream",
            artifact_kind="data",
            size_bytes=len(artifact_bytes),
            sha256=artifact_hash,
            current_version_id=version_id,
            status="ready",
        )
        db.add(artifact)
        await db.flush()
        db.add(
            ArtifactVersion(
                id=version_id,
                artifact_id=artifact_id,
                version_number=1,
                storage_provider="local",
                object_key=str(graph.artifact_version_paths[-1]),
                original_filename="retained.bin",
                size_bytes=len(artifact_bytes),
                sha256=artifact_hash,
            )
        )
        await db.commit()
    return attachment_id, artifact_id, event_id


async def _seed_inactive_workerless_run(fixture: RegisteredLifecycleFixture) -> tuple[UUID, UUID]:
    actors = fixture.actors
    conversation_id, run_id = uuid4(), uuid4()
    async with fixture.sessions() as db:
        inactive = await db.get(User, actors.inactive_admin_id)
        assert inactive is not None and not inactive.is_active
        db.add(
            Conversation(
                id=conversation_id,
                tenant_id=actors.tenant_id,
                org_id=actors.org_id,
                agent_id=None,
                user_id=inactive.id,
                agent_name_snapshot="Retained agent",
                agent_lifecycle_state="purged",
                agent_purged_at=datetime.now(UTC),
                last_activity_at=datetime.now(UTC),
                title="Inactive maintenance fixture",
            )
        )
        run = ConversationRun(
            id=run_id,
            conversation_id=conversation_id,
            agent_id=None,
            user_id=inactive.id,
            source="chat",
            status="canceling",
            is_active=True,
            cancel_requested_at=utc_now_naive(),
        )
        db.add(run)
        await db.commit()
    return conversation_id, run_id


async def run_revoked_owner_maintenance_history(
    fixture: RegisteredLifecycleFixture,
) -> RevokedOwnerProof:
    graph = fixture.graph
    actors = fixture.actors
    attachment_id, artifact_id, history_event_id = await _seed_retained_owner_rows(fixture)
    inactive_conversation_id, inactive_run_id = await _seed_inactive_workerless_run(fixture)
    async with fixture.sessions() as db:
        workerless = await db.get(ConversationRun, graph.run_ids[0], with_for_update=True)
        remote = await db.get(ConversationRun, graph.run_ids[1], with_for_update=True)
        original_input = await db.get(ConversationRunInput, graph.input_ids[0])
        assert workerless is not None and remote is not None and original_input is not None
        immutable_payload = dict(original_input.input_payload)
        await conversation_run_service.request_cancel_run(db, workerless, reason="agent_archived")
        await conversation_run_service.request_cancel_run(db, remote, reason="agent_archived")
        membership = await db.get(OrganizationMember, (actors.org_id, actors.owner_one_id))
        tenant_member = await db.get(TenantMember, (actors.tenant_id, actors.owner_one_id))
        assert membership is not None and tenant_member is not None
        membership.status = "removed"
        tenant_member.status = "removed"
        await db.commit()

    finalized = await finalize_workerless_cancel_before_start(
        session_factory=fixture.sessions,
        protected_run_ids=(),
    )
    assert graph.conversation_ids[0] in finalized
    assert inactive_conversation_id in finalized

    async with fixture.sessions() as db:
        workerless = await db.get(ConversationRun, graph.run_ids[0])
        inactive_run = await db.get(ConversationRun, inactive_run_id)
        remote = await db.get(ConversationRun, graph.run_ids[1])
        input_row = await db.get(ConversationRunInput, graph.input_ids[0])
        assert workerless is not None and inactive_run is not None and remote is not None
        assert input_row is not None and input_row.input_payload == immutable_payload
        assert workerless.status == inactive_run.status == "canceled"
        assert remote.status == "canceling" and remote.worker_instance_id == "fixture-worker"
        assert input_row.status == "canceled" and input_row.run_id is None
        terminal_events = await db.scalar(
            select(func.count())
            .select_from(MessageEvent)
            .where(MessageEvent.assistant_msg_id == str(workerless.id))
        )
        audit_events = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.run_id == str(workerless.id),
                AuditEvent.action == "conversation.run_canceled",
            )
        )
        history = await db.get(MessageEvent, history_event_id)
        attachment = await db.get(MessageAttachment, attachment_id)
        artifact = await db.get(ConversationArtifact, artifact_id)
        assert history is not None and attachment is not None and artifact is not None
        context = await resolve_private_actor(
            db,
            PrivateResourceActor(actors.owner_two_id, actors.org_id, actors.tenant_id),
        )
        await require_private_conversation(db, graph.conversation_ids[1], context)
        attachment_bytes = await AsyncPath(attachment.storage_path).read_bytes()
        version = await db.get(ArtifactVersion, artifact.current_version_id)
        assert version is not None
        artifact_bytes = await AsyncPath(version.object_key).read_bytes()
        try:
            await resolve_private_actor(
                db,
                PrivateResourceActor(actors.owner_one_id, actors.org_id, actors.tenant_id),
            )
        except AppError as error:
            revoked_denial = error.status, error.code
        else:
            raise AssertionError("revoked owner unexpectedly retained private scope")
        serialized_history = str(history.events)
        proof = RevokedOwnerProof(
            revoked_ack=workerless.cancellation_acknowledged_at is not None,
            inactive_ack=inactive_run.cancellation_acknowledged_at is not None,
            remote_ack_absent=remote.cancellation_acknowledged_at is None,
            input_revision=input_row.revision,
            terminal_events=int(terminal_events or 0),
            audit_events=int(audit_events or 0),
            retained_attachment=attachment_bytes,
            retained_artifact=artifact_bytes,
            retained_history_masked=(
                _DUMMY_SECRET not in serialized_history and "<redacted>" in serialized_history
            ),
            revoked_denial=revoked_denial,
        )
        await db.execute(
            delete(Conversation).where(
                Conversation.id.in_((graph.conversation_ids[1], inactive_conversation_id))
            )
        )
        await db.commit()
        return proof
