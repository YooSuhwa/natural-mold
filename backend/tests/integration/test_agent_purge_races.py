from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from anyio import Path as AsyncPath
from sqlalchemy import func, select

from app.agent_runtime import runtime_config
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.services import agent_purge_cleanup, conversation_run_service
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture
from tests.authz.agent_purge_native_support import (
    acknowledge_archived_runs,
    actor_client,
    native_checkpointer,
    seed_missing_purge_checkpoints,
)
from tests.integration.agent_purge_race_support import (
    NativePurgeRacePhase,
    race_same_purge_request,
)

pytestmark = pytest.mark.integration


async def _archive(fixture) -> None:
    async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
        response = await client.delete(
            f"/api/agents/{fixture.graph.root_agent_id}",
            headers={"X-CSRF-Token": csrf},
        )
    assert response.status_code == 204, response.text


async def _purge(fixture):
    async with actor_client(fixture, fixture.actors.direct_org_admin_id) as (client, csrf):
        return await client.post(
            f"/api/agents/{fixture.graph.root_agent_id}/purge",
            json={"confirmation_name": "Lifecycle root"},
            headers={"X-CSRF-Token": csrf},
        )


@pytest.mark.parametrize("phase", ["prepare", "snapshot", "finalize"])
async def test_native_concurrent_same_purge_replays_one_receipt(
    monkeypatch: pytest.MonkeyPatch,
    phase: NativePurgeRacePhase,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        async with native_checkpointer():
            await seed_missing_purge_checkpoints(fixture)
            await _archive(fixture)
            await acknowledge_archived_runs(fixture)
            first, second = await race_same_purge_request(fixture, monkeypatch, phase)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json() == second.json()
        async with fixture.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AgentPurgeReceipt)
                    .where(AgentPurgeReceipt.agent_id_snapshot == fixture.graph.root_agent_id)
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.action == "agent.purge",
                        AuditEvent.target_id == str(fixture.graph.root_agent_id),
                        AuditEvent.actor_user_id == fixture.actors.direct_org_admin_id,
                    )
                )
                == 1
            )


async def test_native_pending_remote_ack_blocks_purge_without_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        # Given: archive has signaled every owner, but no remote terminal ACK has persisted.
        await _archive(fixture)

        # When: an authorized admin requests purge based only on elapsed time.
        blocked = await _purge(fixture)

        # Then: durable execution state wins and transaction A writes nothing.
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "AGENT_LIFECYCLE_EXECUTION_PENDING"
        async with fixture.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AgentPurgeReceipt)
                    .where(AgentPurgeReceipt.agent_id_snapshot == fixture.graph.root_agent_id)
                )
                == 0
            )


async def test_native_legacy_unlinked_envelope_blocks_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        await _archive(fixture)
        await acknowledge_archived_runs(fixture)
        async with fixture.sessions() as db:
            db.add(
                AgentApiRun(
                    id=uuid4(),
                    public_id=f"task14-unlinked-{uuid4()}",
                    user_id=fixture.actors.owner_one_id,
                    deployment_id=None,
                    deployment_public_id_snapshot="task14-legacy",
                    conversation_run_id=None,
                    thread_id=None,
                    conversation_id=fixture.graph.conversation_ids[0],
                    mode="background",
                    status="running",
                )
            )
            await db.commit()

        # When: every owned run is terminal but a legacy envelope remains active and unlinked.
        blocked = await _purge(fixture)

        # Then: the compatibility barrier refuses preparation.
        assert blocked.status_code == 409, blocked.text
        async with fixture.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AgentPurgeReceipt)
                    .where(AgentPurgeReceipt.agent_id_snapshot == fixture.graph.root_agent_id)
                )
                == 0
            )


async def test_native_terminal_commit_barrier_and_cleanup_retry_are_truthful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        owned_root = Path(os.environ["MOLDY_TEST_RUN_ROOT"]) / "task14-race"
        data_root = owned_root / "data"
        monkeypatch.setattr(runtime_config, "_DATA_DIR", data_root)
        config_file = data_root / "agents" / str(graph.root_agent_id) / "AGENTS.md"
        await AsyncPath(config_file.parent).mkdir(parents=True, exist_ok=True)
        await AsyncPath(config_file).write_bytes(b"agent-only-config")
        attachment_before = await AsyncPath(graph.attachment_path).read_bytes()
        version_before = tuple(
            [await AsyncPath(item).read_bytes() for item in graph.artifact_version_paths]
        )

        async with native_checkpointer():
            await seed_missing_purge_checkpoints(fixture)
            await _archive(fixture)
            async with fixture.sessions() as terminal_db:
                for run_id in graph.run_ids[:2]:
                    run = await terminal_db.get(ConversationRun, run_id, with_for_update=True)
                    assert run is not None
                    await conversation_run_service.transition_run(
                        terminal_db,
                        run,
                        "canceled",
                        cancellation_ack_worker_id=run.worker_instance_id,
                        allow_workerless_cancellation_ack=run.worker_instance_id is None,
                    )
                await terminal_db.commit()
                run = await terminal_db.get(ConversationRun, graph.run_ids[2], with_for_update=True)
                assert run is not None
                await conversation_run_service.transition_run(
                    terminal_db,
                    run,
                    "canceled",
                    cancellation_ack_worker_id=run.worker_instance_id,
                )

                # The uncommitted terminal row is invisible to the purge transaction.
                blocked = await _purge(fixture)
                assert blocked.status_code == 409, blocked.text
                await terminal_db.commit()

            original_remove = agent_purge_cleanup._remove_path

            def fail_agent_file_cleanup(path: Path) -> None:
                if path == config_file.parent:
                    raise OSError("dummy cleanup failure")
                original_remove(path)

            monkeypatch.setattr(agent_purge_cleanup, "_remove_path", fail_agent_file_cleanup)
            pending = await _purge(fixture)
            assert pending.status_code == 202, pending.text
            assert pending.json()["state"] == "cleanup_pending"
            receipt_id = UUID(pending.json()["receipt_id"])

            # Database deletion committed before the failed external cleanup.
            async with fixture.sessions() as db:
                assert await db.get(Agent, graph.root_agent_id) is None
                receipt = await db.get(AgentPurgeReceipt, receipt_id)
                assert receipt is not None and receipt.state == "cleanup_pending"
                failed = tuple(
                    await db.scalars(
                        select(AgentPurgeCleanupItem).where(
                            AgentPurgeCleanupItem.receipt_id == receipt.id,
                            AgentPurgeCleanupItem.status == "failed",
                        )
                    )
                )
                assert len(failed) == 1
                assert failed[0].last_error == "external cleanup failed"
            assert await AsyncPath(config_file).read_bytes() == b"agent-only-config"

            monkeypatch.setattr(agent_purge_cleanup, "_remove_path", original_remove)
            completed = await _purge(fixture)
            assert completed.status_code == 200, completed.text
            assert UUID(completed.json()["receipt_id"]) == receipt_id
            assert completed.json()["state"] == "completed"

        # Replay performs no second audit or relational delete and preserves history bytes.
        async with fixture.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AgentPurgeReceipt)
                    .where(AgentPurgeReceipt.agent_id_snapshot == graph.root_agent_id)
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.action == "agent.purge",
                        AuditEvent.target_id == str(graph.root_agent_id),
                        AuditEvent.actor_user_id == fixture.actors.direct_org_admin_id,
                    )
                )
                == 1
            )
        assert not await AsyncPath(config_file.parent).exists()
        assert hashlib.sha256(await AsyncPath(graph.attachment_path).read_bytes()).digest() == (
            hashlib.sha256(attachment_before).digest()
        )
        assert (
            tuple([await AsyncPath(item).read_bytes() for item in graph.artifact_version_paths])
            == version_before
        )
