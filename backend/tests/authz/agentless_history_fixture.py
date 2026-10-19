from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
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
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.models.token_usage import TokenUsage
from app.models.user import User


@dataclass(frozen=True, slots=True)
class NativeHistorySeed:
    conversation_id: UUID
    run_id: UUID
    artifact_id: UUID
    api_agent_id: UUID


@dataclass(frozen=True, slots=True)
class HistorySurfaces:
    history: Response
    messages: Response
    state: Response
    traces: Response
    active_run: Response
    run: Response
    inputs: Response
    files: Response
    artifact: Response
    download: Response


async def seed_native_history(
    db: AsyncSession,
    user: User,
    storage: Path,
) -> NativeHistorySeed:
    membership = await db.get(OrganizationMember, (user.last_active_org_id, user.id))
    assert membership is not None
    storage.joinpath("native-retained.txt").write_bytes(b"native-retained-history")
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    api_agent = Agent(
        user_id=user.id,
        org_id=membership.org_id,
        tenant_id=membership.tenant_id,
        model_id=model.id,
        name="Public API fixture",
        system_prompt="Fixture",
    )
    db.add(api_agent)
    await db.flush()
    now_aware = datetime.now(UTC)
    now_naive = now_aware.replace(tzinfo=None)
    run_id, artifact_id, version_id = uuid4(), uuid4(), uuid4()
    conversation = Conversation(
        agent_id=None,
        user_id=user.id,
        org_id=membership.org_id,
        tenant_id=membership.tenant_id,
        agent_name_snapshot="JWT retained agent",
        agent_lifecycle_state="purged",
        agent_archived_at_snapshot=now_aware,
        agent_purged_at=now_aware,
        title="JWT retained history",
        last_activity_at=now_aware,
    )
    db.add(conversation)
    await db.flush()
    manifest = ConversationHistoryManifest(
        conversation_id=conversation.id,
        source_agent_id_snapshot=uuid4(),
        state="complete",
        checkpoint_count=1,
        event_count=1,
        canonical_payload_sha256="a" * 64,
    )
    run = ConversationRun(
        id=run_id,
        conversation_id=conversation.id,
        agent_id=None,
        user_id=user.id,
        source="chat",
        status="completed",
        is_active=False,
        input_preview="native retained input",
        completed_at=now_naive,
    )
    artifact = ConversationArtifact(
        id=artifact_id,
        user_id=user.id,
        org_id=membership.org_id,
        tenant_id=membership.tenant_id,
        agent_id=None,
        conversation_id=conversation.id,
        assistant_msg_id=str(run_id),
        logical_path="reports/native-retained.txt",
        display_name="native-retained.txt",
        mime_type="text/plain",
        artifact_kind="document",
        size_bytes=23,
        sha256="c" * 64,
        current_version_id=version_id,
        status="ready",
    )
    db.add_all([manifest, run, artifact])
    await db.flush()
    db.add_all(
        [
            ConversationHistoryCheckpoint(
                manifest_id=manifest.id,
                conversation_id=conversation.id,
                checkpoint_id="jwt-retained",
                parent_checkpoint_id=None,
                checkpoint_namespace="",
                channel_values={"safe": "[REDACTED]"},
                messages=[{"type": "human", "content": "[REDACTED]"}],
                branch={"active": True, "parent_checkpoint_id": None},
                trace={"references": []},
                payload_sha256="b" * 64,
            ),
            ConversationHistoryEvent(
                manifest_id=manifest.id,
                conversation_id=conversation.id,
                assistant_message_id=str(run_id),
                sequence_start=0,
                sequence_end=0,
                events=[{"event": "message_end", "data": {"content": "[REDACTED]"}}],
                payload_sha256="d" * 64,
            ),
            ConversationRunMetrics(
                run_id=run_id,
                terminal_state="completed",
                prompt_tokens=11,
                completion_tokens=5,
                estimated_cost=Decimal("0.87654321"),
                usage_complete=True,
            ),
            ConversationRunInput(
                conversation_id=conversation.id,
                agent_id=None,
                user_id=user.id,
                client_request_id="native-retained-input",
                source="ui",
                status="canceled",
                priority=0,
                position=1,
                revision=3,
                input_payload={"message": "native retained"},
                attachment_ids=[],
            ),
            TokenUsage(
                run_id=run_id,
                conversation_id=conversation.id,
                agent_id=None,
                model_name="recorded-native-model",
                prompt_tokens=11,
                completion_tokens=5,
                total_tokens=16,
                estimated_cost=Decimal("0.876543"),
            ),
            MessageAttachment(
                user_id=user.id,
                org_id=membership.org_id,
                tenant_id=membership.tenant_id,
                conversation_id=conversation.id,
                message_id="native-message",
                filename="native-input.txt",
                mime_type="text/plain",
                size_bytes=19,
                storage_path="native/input.txt",
                url=f"/api/uploads/{uuid4()}/content",
            ),
            ArtifactVersion(
                id=version_id,
                artifact_id=artifact_id,
                version_number=1,
                storage_provider="local",
                object_key="native-retained.txt",
                original_filename="native-retained.txt",
                size_bytes=23,
                sha256="c" * 64,
            ),
        ]
    )
    await db.commit()
    return NativeHistorySeed(conversation.id, run_id, artifact_id, api_agent.id)


async def read_history_surfaces(client: AsyncClient, seed: NativeHistorySeed) -> HistorySurfaces:
    prefix = f"/api/conversations/{seed.conversation_id}"
    return HistorySurfaces(
        history=await client.get(f"{prefix}/history"),
        messages=await client.get(f"{prefix}/messages"),
        state=await client.get(f"{prefix}/langgraph/threads/{seed.conversation_id}/state"),
        traces=await client.get(f"{prefix}/traces"),
        active_run=await client.get(f"{prefix}/runs/active"),
        run=await client.get(f"{prefix}/runs/{seed.run_id}"),
        inputs=await client.get(f"{prefix}/run-inputs"),
        files=await client.get(f"{prefix}/files"),
        artifact=await client.get(f"{prefix}/artifacts/{seed.artifact_id}"),
        download=await client.get(f"{prefix}/artifacts/{seed.artifact_id}/download"),
    )


def assert_history_surfaces(surfaces: HistorySurfaces) -> None:
    assert surfaces.history.status_code == 200, surfaces.history.text
    assert surfaces.history.json()["messages"][0]["content"] == "[REDACTED]"
    assert surfaces.history.json()["runs"][0]["metrics"]["estimated_cost"] == 0.87654321
    assert surfaces.history.json()["usage"][0]["estimated_cost"] == 0.876543
    assert surfaces.messages.status_code == 200, surfaces.messages.text
    assert surfaces.messages.json()["messages"][0]["content"] == "[REDACTED]"
    assert surfaces.state.status_code == 200, surfaces.state.text
    assert surfaces.state.json()["values"]["safe"] == "[REDACTED]"
    assert surfaces.traces.status_code == 200, surfaces.traces.text
    assert surfaces.traces.json()[0]["events"][0]["data"]["content"] == "[REDACTED]"
    assert surfaces.active_run.status_code == 200, surfaces.active_run.text
    assert surfaces.active_run.json() is None
    assert surfaces.run.status_code == 200, surfaces.run.text
    assert surfaces.run.json()["agent_id"] is None
    assert surfaces.run.json()["metrics"]["estimated_cost"] == 0.87654321
    assert surfaces.inputs.status_code == 200, surfaces.inputs.text
    assert surfaces.inputs.json()["items"][0]["revision"] == 3
    assert surfaces.files.status_code == 200, surfaces.files.text
    assert {item["size_bytes"] for item in surfaces.files.json()} == {19, 23}
    assert surfaces.artifact.status_code == 200, surfaces.artifact.text
    assert surfaces.artifact.json()["agent_name"] == "JWT retained agent"
    assert surfaces.download.status_code == 200, surfaces.download.text
    assert surfaces.download.content == b"native-retained-history"
