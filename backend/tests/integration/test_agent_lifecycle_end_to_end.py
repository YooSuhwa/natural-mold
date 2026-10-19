"""Task 15 single-flow native witness for the complete B08 lifecycle."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from anyio import Path as AsyncPath
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from pydantic import TypeAdapter
from sqlalchemy import select

from app.agent_runtime.checkpointer import get_checkpointer
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.schemas.agent_lifecycle import (
    AgentLifecycleMetadataResponse,
    AgentPurgeReceiptResponse,
    AgentRestoreResponse,
)
from app.schemas.conversation_history import (
    ConversationHistoryCheckpointResponse,
    ConversationHistoryResponse,
)
from app.services import agent_purge_cleanup
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture
from tests.authz.agent_purge_native_support import (
    actor_client,
    native_checkpointer,
    seed_missing_purge_checkpoints,
)
from tests.integration.agent_lifecycle_e2e_retention import capture_pre_purge_retention
from tests.integration.agent_lifecycle_e2e_support import (
    SECRET_MARKER,
    SECRET_USERNAME,
    assert_purged_graph,
    assert_secret_masked,
    assert_stopped_graph,
    prepare_witness,
)

pytestmark = pytest.mark.integration


async def test_registered15_cross_surface_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive one two-owner graph through archive, restore, rearchive, and purge."""
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        baseline = await prepare_witness(fixture, monkeypatch)
        assert graph.root_owner_id != graph.child_owner_id
        async with native_checkpointer():
            await seed_missing_purge_checkpoints(fixture)
            saver = get_checkpointer()
            branch_config: RunnableConfig = {
                "configurable": {
                    "thread_id": str(graph.conversation_ids[0]),
                    "checkpoint_id": graph.checkpoint_ids[-1],
                    "checkpoint_ns": "",
                }
            }
            branch = await saver.aget_tuple(branch_config)
            assert branch is not None
            secret_checkpoint = empty_checkpoint()
            secret_checkpoint_id = f"task15-secret-{uuid4().hex}"
            secret_checkpoint["id"] = secret_checkpoint_id
            secret_checkpoint["channel_values"] = {
                "messages": [
                    HumanMessage(id="task15-user", content="Task 15 secret witness"),
                    AIMessage(
                        id="task15-assistant",
                        content=f"credential={SECRET_USERNAME}:{SECRET_MARKER}",
                    ),
                ],
                "branch_header": {
                    "name": "task15-secret",
                    "parent": graph.checkpoint_ids[-1],
                },
            }
            secret_checkpoint["channel_versions"] = {
                "messages": "3",
                "branch_header": "3",
            }
            await saver.aput(
                branch.config,
                secret_checkpoint,
                {"source": "loop", "step": 2},
                {"messages": "3", "branch_header": "3"},
            )

            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (
                client,
                csrf,
            ):
                archived = await client.delete(
                    f"/api/agents/{graph.root_agent_id}",
                    headers={"X-CSRF-Token": csrf},
                )
                assert archived.status_code == 204, archived.text
                archived_list = await client.get("/api/agents/archived")
                assert archived_list.status_code == 200, archived_list.text
                metadata = next(
                    item
                    for item in TypeAdapter(list[AgentLifecycleMetadataResponse]).validate_json(
                        archived_list.content
                    )
                    if item.agent_id == graph.root_agent_id
                )
                assert metadata.lifecycle_state == "archived_stopping"

            recovered = await finalize_workerless_cancel_before_start(
                session_factory=fixture.sessions,
                protected_run_ids=(),
            )
            assert set(graph.conversation_ids).issubset(set(recovered))
            await assert_stopped_graph(fixture, baseline)
            retention_proof = await capture_pre_purge_retention(fixture, baseline.proof)

            async with actor_client(fixture, fixture.actors.owner_one_id) as (client, csrf):
                restored_response = await client.post(
                    f"/api/agents/{graph.root_agent_id}/restore",
                    headers={"X-CSRF-Token": csrf},
                )
                assert restored_response.status_code == 200, restored_response.text
                restored = AgentRestoreResponse.model_validate_json(restored_response.content)
                assert restored.lifecycle_state == "active"

            await assert_stopped_graph(fixture, baseline)

            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (
                client,
                csrf,
            ):
                rearchived = await client.delete(
                    f"/api/agents/{graph.root_agent_id}",
                    headers={"X-CSRF-Token": csrf},
                )
                assert rearchived.status_code == 204, rearchived.text
                stopped_list = await client.get("/api/agents/archived")
                stopped = next(
                    item
                    for item in TypeAdapter(list[AgentLifecycleMetadataResponse]).validate_json(
                        stopped_list.content
                    )
                    if item.agent_id == graph.root_agent_id
                )
                assert stopped.lifecycle_state == "archived_stopped"
                revoked_share = await client.get(
                    f"/api/shares/b08-share-{graph.root_agent_id.hex[:12]}"
                )
                assert revoked_share.status_code == 404

            original_remove = agent_purge_cleanup._remove_path

            def fail_agent_cleanup(path: Path) -> None:
                if path == baseline.agent_root:
                    raise OSError("task15 dummy cleanup failure")
                original_remove(path)

            monkeypatch.setattr(agent_purge_cleanup, "_remove_path", fail_agent_cleanup)
            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (
                client,
                csrf,
            ):
                pending_response = await client.post(
                    f"/api/agents/{graph.root_agent_id}/purge",
                    json={"confirmation_name": "Lifecycle root"},
                    headers={"X-CSRF-Token": csrf},
                )
            assert pending_response.status_code == 202, pending_response.text
            pending = AgentPurgeReceiptResponse.model_validate_json(pending_response.content)
            assert pending.state == "cleanup_pending"
            assert await AsyncPath(baseline.config_file).read_bytes() == (
                b"task15-agent-only-config"
            )
            async with fixture.sessions() as db:
                receipt = await db.get(AgentPurgeReceipt, pending.receipt_id)
                failed_items = tuple(
                    await db.scalars(
                        select(AgentPurgeCleanupItem).where(
                            AgentPurgeCleanupItem.receipt_id == pending.receipt_id,
                            AgentPurgeCleanupItem.status == "failed",
                        )
                    )
                )
                assert receipt is not None and receipt.state == "cleanup_pending"
                assert len(failed_items) == 1
                assert failed_items[0].last_error == "external cleanup failed"

            monkeypatch.setattr(agent_purge_cleanup, "_remove_path", original_remove)
            async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (
                client,
                csrf,
            ):
                completed_response = await client.post(
                    f"/api/agents/{graph.root_agent_id}/purge",
                    json={"confirmation_name": "Lifecycle root"},
                    headers={"X-CSRF-Token": csrf},
                )
            assert completed_response.status_code == 200, completed_response.text
            completed = AgentPurgeReceiptResponse.model_validate_json(completed_response.content)
            assert completed.receipt_id == pending.receipt_id
            assert completed.state == "completed"

            history: list[ConversationHistoryResponse] = []
            owner_cases = (
                (fixture.actors.owner_one_id, graph.conversation_ids[0]),
                (fixture.actors.owner_two_id, graph.conversation_ids[1]),
                (fixture.actors.owner_one_id, graph.conversation_ids[2]),
            )
            for owner_id, conversation_id in owner_cases:
                async with actor_client(fixture, owner_id) as (client, _csrf):
                    response = await client.get(f"/api/conversations/{conversation_id}/history")
                assert response.status_code == 200, response.text
                history.append(ConversationHistoryResponse.model_validate_json(response.content))

            assert {item.conversation.id for item in history} == set(graph.conversation_ids)
            assert {run.id for item in history for run in item.runs} == set(graph.run_ids)
            assert {item.id for body in history for item in body.inputs} == set(graph.input_ids)
            assert all(
                item.conversation.agent_id is None
                and item.conversation.lifecycle_state == "purged"
                and item.conversation.read_only
                and item.manifest is not None
                and item.manifest.state == "complete"
                for item in history
            )
            assert {
                item.conversation.id: item.manifest.checkpoint_count
                for item in history
                if item.manifest is not None
            } == {
                graph.conversation_ids[0]: 3,
                graph.conversation_ids[1]: 1,
                graph.conversation_ids[2]: 1,
            }
            serialized = TypeAdapter(list[ConversationHistoryResponse]).dump_json(history).decode()
            assert_secret_masked(serialized)
            root_history = next(
                item for item in history if item.conversation.id == graph.conversation_ids[0]
            )
            checkpoints = TypeAdapter(list[ConversationHistoryCheckpointResponse]).validate_python(
                root_history.checkpoints
            )
            assert {item.checkpoint_id for item in checkpoints} == {
                *graph.checkpoint_ids,
                secret_checkpoint_id,
            }
            checkpoint_bytes = TypeAdapter(list[ConversationHistoryCheckpointResponse]).dump_json(
                checkpoints
            )
            assert b"ui://fixture/static" in checkpoint_bytes
            assert str(graph.mcp_binding_id).encode() in checkpoint_bytes
            assert str(graph.run_ids[0]).encode() in checkpoint_bytes

        await assert_purged_graph(fixture, baseline, retention_proof)
        assert fixture.sentinel.provider_invocations == 0
