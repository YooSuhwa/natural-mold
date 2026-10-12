"""Focused setup and SQL observations for the Task 15 lifecycle witness."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from anyio import Path as AsyncPath
from sqlalchemy import select

from app.agent_runtime import runtime_config
from app.config import settings
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryManifest
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.share_link import ShareLink
from tests.authz.agent_lifecycle_fixture import (
    LifecycleGraphProof,
    RegisteredLifecycleFixture,
)
from tests.authz.agent_lifecycle_graph_seed import assert_lifecycle_graph
from tests.integration.agent_purge_retention_assertions import (
    assert_purge_retention_inventory,
)

SECRET_MARKER = "actual-marker-secret"
SECRET_USERNAME = "task15-user-secret"


@dataclass(frozen=True, slots=True)
class WitnessBaseline:
    proof: LifecycleGraphProof
    deployment_id: UUID
    trigger_id: UUID
    agent_root: Path
    config_file: Path
    offload_file: Path
    attachment: bytes
    artifact_versions: tuple[bytes, bytes]


async def prepare_witness(
    fixture: RegisteredLifecycleFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> WitnessBaseline:
    """Normalize all accepted fixture work to genuine queued/null-owner state."""
    graph = fixture.graph
    data_root = Path(os.environ["MOLDY_TEST_RUN_ROOT"]) / "task15-witness" / "data"
    agent_root = data_root / "agents" / str(graph.root_agent_id)
    config_file = agent_root / "AGENTS.md"
    offload_file = data_root / ".moldy" / "offload" / "conversation-owned.bin"
    monkeypatch.setattr(runtime_config, "_DATA_DIR", data_root)
    monkeypatch.setattr(settings, "agent_image_dir", str(data_root / "agents"))
    await AsyncPath(config_file.parent).mkdir(parents=True, exist_ok=True)
    await AsyncPath(config_file).write_bytes(b"task15-agent-only-config")
    await AsyncPath(offload_file.parent).mkdir(parents=True, exist_ok=True)
    await AsyncPath(offload_file).write_bytes(b"task15-conversation-offload")

    async with fixture.sessions() as db:
        root = await db.get(Agent, graph.root_agent_id)
        assert root is not None
        model = await db.get(Model, root.model_id)
        assert model is not None
        model.base_url = f"http://{SECRET_USERNAME}:{SECRET_MARKER}@example.invalid"
        for run_id in graph.run_ids:
            run = await db.get(ConversationRun, run_id, with_for_update=True)
            assert run is not None
            run.status = "queued"
            run.is_active = True
            run.worker_instance_id = None
            run.started_at = None
            run.heartbeat_at = None
            run.cancel_requested_at = None
            run.cancel_reason = None
            run.cancellation_acknowledged_at = None
            run.completed_at = None
        api_run = await db.get(AgentApiRun, graph.api_run_id)
        trigger_run = await db.get(AgentTriggerRun, graph.trigger_run_id)
        assert api_run is not None and api_run.deployment_id is not None
        assert trigger_run is not None and trigger_run.trigger_id is not None
        deployment_id = api_run.deployment_id
        trigger_id = trigger_run.trigger_id
        api_run.status = "pending"
        trigger_run.status = "queued"
        await db.commit()

    proof = await assert_lifecycle_graph(fixture.sessions, graph)
    attachment = await AsyncPath(graph.attachment_path).read_bytes()
    versions = (
        await AsyncPath(graph.artifact_version_paths[0]).read_bytes(),
        await AsyncPath(graph.artifact_version_paths[1]).read_bytes(),
    )
    return WitnessBaseline(
        proof=proof,
        deployment_id=deployment_id,
        trigger_id=trigger_id,
        agent_root=agent_root,
        config_file=config_file,
        offload_file=offload_file,
        attachment=attachment,
        artifact_versions=versions,
    )


async def assert_stopped_graph(
    fixture: RegisteredLifecycleFixture,
    baseline: WitnessBaseline,
) -> None:
    """Prove workerless recovery stopped chat/API/trigger without a synthetic owner."""
    graph = fixture.graph
    async with fixture.sessions() as db:
        runs = tuple(
            await db.scalars(
                select(ConversationRun)
                .where(ConversationRun.id.in_(graph.run_ids))
                .order_by(ConversationRun.id)
            )
        )
        api_run = await db.get(AgentApiRun, graph.api_run_id)
        trigger_run = await db.get(AgentTriggerRun, graph.trigger_run_id)
        deployment = await db.get(AgentDeployment, baseline.deployment_id)
        trigger = await db.get(AgentTrigger, baseline.trigger_id)
        share = await db.get(ShareLink, graph.share_link_id)
        links = tuple(
            await db.scalars(
                select(AgentSubAgentLink).where(
                    AgentSubAgentLink.parent_agent_id == graph.root_agent_id
                )
            )
        )
        assert len(runs) == 3
        assert all(
            run.status == "canceled"
            and not run.is_active
            and run.worker_instance_id is None
            and run.cancel_requested_at is not None
            and run.cancellation_acknowledged_at is not None
            for run in runs
        )
        assert api_run is not None and api_run.status == "canceled"
        assert trigger_run is not None and trigger_run.status == "canceled"
        assert deployment is not None and deployment.status == "disabled"
        assert trigger is not None and trigger.status == "paused"
        assert share is not None and share.revoked_at is not None
        assert links == ()


async def assert_purged_graph(
    fixture: RegisteredLifecycleFixture,
    baseline: WitnessBaseline,
    retention_proof: LifecycleGraphProof,
) -> None:
    """Prove exact retained identities, bytes, receipt replay, and audit identity."""
    graph = fixture.graph
    async with fixture.sessions() as db:
        conversations = tuple(
            await db.scalars(
                select(Conversation).where(Conversation.id.in_(graph.conversation_ids))
            )
        )
        receipt = await db.scalar(
            select(AgentPurgeReceipt).where(
                AgentPurgeReceipt.agent_id_snapshot == graph.root_agent_id
            )
        )
        assert receipt is not None and receipt.state == "completed"
        cleanup = tuple(
            await db.scalars(
                select(AgentPurgeCleanupItem).where(AgentPurgeCleanupItem.receipt_id == receipt.id)
            )
        )
        manifests = tuple(
            await db.scalars(
                select(ConversationHistoryManifest).where(
                    ConversationHistoryManifest.conversation_id.in_(graph.conversation_ids)
                )
            )
        )
        audits = tuple(
            await db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "agent.purge",
                    AuditEvent.target_id == str(graph.root_agent_id),
                )
            )
        )
        assert await db.get(Agent, graph.root_agent_id) is None
        assert {row.id for row in conversations} == set(graph.conversation_ids)
        assert all(
            row.agent_id is None
            and row.agent_lifecycle_state == "purged"
            and row.agent_name_snapshot == "Lifecycle root"
            for row in conversations
        )
        assert len(manifests) == 3
        assert all(
            row.state == "complete"
            and len(row.canonical_payload_sha256) == 64
            and row.source_agent_id_snapshot == graph.root_agent_id
            for row in manifests
        )
        assert cleanup and all(row.status == "succeeded" for row in cleanup)
        assert len(audits) == 1
        assert audits[0].actor_user_id == fixture.actors.direct_org_admin_id
        assert audits[0].target_owner_user_id == fixture.actors.owner_one_id
        await assert_purge_retention_inventory(db, graph, retention_proof)

    attachment = await AsyncPath(graph.attachment_path).read_bytes()
    versions = (
        await AsyncPath(graph.artifact_version_paths[0]).read_bytes(),
        await AsyncPath(graph.artifact_version_paths[1]).read_bytes(),
    )
    assert hashlib.sha256(attachment).hexdigest() == baseline.proof.attachment_sha256
    assert tuple(hashlib.sha256(item).hexdigest() for item in versions) == (
        baseline.proof.artifact_version_sha256
    )
    assert attachment == baseline.attachment
    assert versions == baseline.artifact_versions
    assert not await AsyncPath(baseline.agent_root).exists()
    assert await AsyncPath(baseline.offload_file).read_bytes() == b"task15-conversation-offload"


def assert_secret_masked(serialized_history: str) -> None:
    """Require the seeded credential marker to be replaced in retained history."""
    assert SECRET_USERNAME not in serialized_history
    assert SECRET_MARKER not in serialized_history
    assert "<redacted>" in serialized_history


__all__ = [
    "WitnessBaseline",
    "SECRET_MARKER",
    "assert_purged_graph",
    "assert_secret_masked",
    "assert_stopped_graph",
    "prepare_witness",
]
