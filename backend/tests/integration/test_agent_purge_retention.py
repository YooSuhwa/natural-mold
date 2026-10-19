from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from anyio import Path as AsyncPath
from anyio import fail_after
from langchain_core.runnables import RunnableConfig
from sqlalchemy import func, select

from app.agent_runtime import runtime_config
from app.agent_runtime.checkpointer import get_checkpointer
from app.config import settings
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentApiKeyDeployment, AgentApiRun, AgentApiThread
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryManifest,
)
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.share_link import ShareLink
from app.services import conversation_history_snapshot
from app.services.agent_lifecycle_projection import lock_lifecycle_target
from app.services.conversation_history_materializer import HistoryProjectionRequest
from app.services.conversation_history_types import HistoryProjection
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture
from tests.authz.agent_lifecycle_graph_seed import assert_lifecycle_graph
from tests.authz.agent_purge_native_support import (
    acknowledge_archived_runs,
    actor_client,
    native_checkpointer,
    seed_missing_purge_checkpoints,
)
from tests.integration.agent_purge_retention_assertions import (
    assert_purge_retention_inventory,
)


async def test_native_purge_preserves_exact_two_owner_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a complete two-owner graph, independently owned key, Agent files, and offload bytes.
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        before = await assert_lifecycle_graph(fixture.sessions, graph)
        owned_root = Path(os.environ["MOLDY_TEST_RUN_ROOT"]) / "task14-retention"
        data_root = owned_root / "data"
        monkeypatch.setattr(runtime_config, "_DATA_DIR", data_root)
        monkeypatch.setattr(settings, "agent_image_dir", str(data_root / "agents"))
        agent_root = data_root / "agents" / str(graph.root_agent_id)
        avatar = agent_root / "avatar.png"
        offload = data_root / ".moldy" / "offload" / "conversation-owned.bin"
        await AsyncPath(avatar.parent).mkdir(parents=True, exist_ok=True)
        await AsyncPath(avatar).write_bytes(b"agent-avatar")
        await AsyncPath(offload.parent).mkdir(parents=True, exist_ok=True)
        await AsyncPath(offload).write_bytes(b"conversation-offload")
        key_id = uuid4()
        link_id = uuid4()
        async with fixture.sessions() as db:
            deployment = await db.scalar(
                select(AgentApiThread.deployment_id).where(
                    AgentApiThread.conversation_id == graph.conversation_ids[1]
                )
            )
            root = await db.get(Agent, graph.root_agent_id)
            assert deployment is not None and root is not None
            model = await db.get(Model, root.model_id)
            assert model is not None
            model.base_url = "http://history-1@example.invalid"
            root.image_path = str(avatar)
            db.add(
                AgentApiKey(
                    id=key_id,
                    tenant_id=fixture.actors.tenant_id,
                    org_id=fixture.actors.org_id,
                    user_id=fixture.actors.owner_two_id,
                    name="Independent history key",
                    key_id=f"task14-{key_id.hex}",
                    key_hash="b" * 64,
                    prefix="moldy_task14",
                    last_four="t014",
                    scopes=["runs:read"],
                    allow_all_deployments=False,
                )
            )
            db.add(
                AgentApiKeyDeployment(
                    id=link_id,
                    api_key_id=key_id,
                    deployment_id=deployment,
                )
            )
            await db.commit()

        async with native_checkpointer():
            await seed_missing_purge_checkpoints(fixture)
            original_projection = conversation_history_snapshot.build_history_projection
            projection_calls = 0

            async def assert_prior_lock_released(
                source: HistoryProjectionRequest,
            ) -> HistoryProjection:
                nonlocal projection_calls
                projection_calls += 1
                if projection_calls == 2:
                    with fail_after(3):
                        async with fixture.sessions() as lock_db:
                            await lock_lifecycle_target(lock_db, graph.root_agent_id)
                            await lock_db.rollback()
                return await original_projection(source)

            monkeypatch.setattr(
                conversation_history_snapshot,
                "build_history_projection",
                assert_prior_lock_released,
            )
            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
                archived = await client.delete(
                    f"/api/agents/{graph.root_agent_id}", headers={"X-CSRF-Token": csrf}
                )
                assert archived.status_code == 204, archived.text
            await acknowledge_archived_runs(fixture)

            # When: the active direct org-admin completes typed-name purge.
            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
                purged = await client.post(
                    f"/api/agents/{graph.root_agent_id}/purge",
                    json={"confirmation_name": "Lifecycle root"},
                    headers={"X-CSRF-Token": csrf},
                )
            assert purged.status_code == 200, purged.text
            assert projection_calls == 3

            # Then: exact retained identities and byte hashes survive in a fresh session.
            async with fixture.sessions() as db:
                conversations = tuple(
                    await db.scalars(
                        select(Conversation).where(Conversation.id.in_(graph.conversation_ids))
                    )
                )
                runs = tuple(
                    await db.scalars(
                        select(ConversationRun).where(ConversationRun.id.in_(graph.run_ids))
                    )
                )
                inputs = tuple(
                    await db.scalars(
                        select(ConversationRunInput).where(
                            ConversationRunInput.id.in_(graph.input_ids)
                        )
                    )
                )
                assert await db.get(Agent, graph.root_agent_id) is None
                children = tuple([await db.get(Agent, item) for item in graph.child_agent_ids])
                assert all(item is not None for item in children)
                assert len(conversations) == 3
                assert all(
                    row.agent_id is None
                    and row.agent_lifecycle_state == "purged"
                    and row.agent_name_snapshot == "Lifecycle root"
                    for row in conversations
                )
                assert {row.id for row in runs} == set(graph.run_ids)
                assert {row.id for row in inputs} == set(graph.input_ids)
                assert all(row.agent_id is None for row in (*runs, *inputs))
                participants = tuple(
                    await db.scalars(
                        select(ConversationRunAgent).where(
                            ConversationRunAgent.run_id.in_(graph.run_ids)
                        )
                    )
                )
                assert len(participants) == 9
                assert all(
                    row.agent_id is None
                    for row in participants
                    if row.agent_id_snapshot == graph.root_agent_id
                )
                assert await db.get(MessageAttachment, graph.attachment_id) is not None
                assert await db.get(ConversationArtifact, graph.artifact_id) is not None
                versions_rows = tuple(
                    [await db.get(ArtifactVersion, item) for item in graph.artifact_version_ids]
                )
                assert all(item is not None for item in versions_rows)
                api_run = await db.get(AgentApiRun, graph.api_run_id)
                trigger_run = await db.get(AgentTriggerRun, graph.trigger_run_id)
                binding = await db.get(McpAppInvocationBinding, graph.mcp_binding_id)
                assert api_run is not None and api_run.deployment_id is None
                assert trigger_run is not None and trigger_run.trigger_id is None
                assert trigger_run.agent_id is None
                assert binding is not None and binding.agent_id is None
                await assert_purge_retention_inventory(db, graph, before)
                assert await db.get(ShareLink, graph.share_link_id) is None
                assert await db.get(AgentApiKey, key_id) is not None
                assert await db.get(AgentApiKeyDeployment, link_id) is None
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(ConversationHistoryManifest)
                        .where(
                            ConversationHistoryManifest.conversation_id.in_(graph.conversation_ids),
                            ConversationHistoryManifest.state == "complete",
                        )
                    )
                    == 3
                )
                receipt = await db.scalar(
                    select(AgentPurgeReceipt).where(
                        AgentPurgeReceipt.agent_id_snapshot == graph.root_agent_id
                    )
                )
                assert receipt is not None and receipt.state == "completed"
                items = tuple(
                    await db.scalars(
                        select(AgentPurgeCleanupItem).where(
                            AgentPurgeCleanupItem.receipt_id == receipt.id
                        )
                    )
                )
                assert items and all(item.status == "succeeded" for item in items)
                history_rows = tuple(
                    await db.scalars(
                        select(ConversationHistoryCheckpoint).where(
                            ConversationHistoryCheckpoint.conversation_id.in_(
                                graph.conversation_ids
                            )
                        )
                    )
                )
                retained_payload = repr(
                    [(row.channel_values, row.messages, row.trace) for row in history_rows]
                )
                assert len(history_rows) == 4
                assert "history-1" not in retained_payload
                assert "<redacted>" in retained_payload
                assert "ui://fixture/static" in retained_payload
            attachment = await AsyncPath(graph.attachment_path).read_bytes()
            versions = tuple(
                [await AsyncPath(path).read_bytes() for path in graph.artifact_version_paths]
            )
            assert hashlib.sha256(attachment).hexdigest() == before.attachment_sha256
            assert tuple(hashlib.sha256(item).hexdigest() for item in versions) == (
                before.artifact_version_sha256
            )
            assert await AsyncPath(offload).read_bytes() == b"conversation-offload"
            assert not await AsyncPath(agent_root).exists()
            checkpointer = get_checkpointer()
            for conversation_id in graph.conversation_ids:
                config: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
                rows = [row async for row in checkpointer.alist(config)]
                assert rows == []
