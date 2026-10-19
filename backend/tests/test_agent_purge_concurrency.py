"""Focused same-request Agent purge concurrency regressions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from anyio import create_task_group, fail_after
from httpx import AsyncClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryManifest
from app.models.conversation_run import ConversationRun
from app.models.user import User
from app.services import agent_purge_cleanup, agent_purge_service
from app.services.agent_purge_service import purge_agent
from app.services.agent_purge_types import AgentPurgeCommand
from app.services.conversation_history_snapshot import (
    HistorySnapshotIncompleteError,
    materialize_conversation_history,
)
from app.services.conversation_history_types import ConversationHistoryMaterializationRequest
from tests.agent_purge_cleanup_cases import RecordingSaver
from tests.agent_purge_concurrency_cases import (
    PurgeRacePhase,
    assert_prepare_rechecks_after_locked_delete,
    assert_same_request_replays_after_competing_delete,
    assert_snapshot_rechecks_after_competing_materialization,
)
from tests.agent_purge_service_cases import (
    FocusedPurgeHistory,
    grant_test_org_admin,
    seed_focused_purge_history,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration.agent_purge_race_support import PurgeRaceGate


async def _add_second_purge_conversation(history: FocusedPurgeHistory) -> None:
    async with TestSession() as setup_db:
        original = await setup_db.get(Conversation, history.conversation_id)
        assert original is not None
        second = Conversation(
            agent_id=history.agent_id,
            user_id=original.user_id,
            tenant_id=original.tenant_id,
            org_id=original.org_id,
            agent_name_snapshot="History purge",
            last_activity_at=datetime.now(UTC),
            title="second retained private title",
        )
        setup_db.add(second)
        await setup_db.flush()
        run = ConversationRun(
            conversation_id=second.id,
            agent_id=history.agent_id,
            user_id=original.user_id,
            source="chat",
            status="completed",
            is_active=False,
        )
        setup_db.add(run)
        await setup_db.flush()
        setup_db.add(
            ConversationRunAgent(
                run_id=run.id,
                agent_id=history.agent_id,
                agent_id_snapshot=history.agent_id,
                role="root",
                path=[],
            )
        )
        await setup_db.commit()
        second_id = second.id
    checkpoint = empty_checkpoint()
    checkpoint["id"] = "purge-checkpoint-second"
    checkpoint["channel_values"] = {
        "messages": [HumanMessage(id="purge-message-second", content="retained second content")]
    }
    checkpoint["channel_versions"] = {"messages": "1"}
    await history.saver.aput(
        {"configurable": {"thread_id": str(second_id), "checkpoint_ns": ""}},
        checkpoint,
        {"source": "input", "step": 0},
        {"messages": "1"},
    )


@pytest.mark.parametrize("phase", ["snapshot", "finalize"])
async def test_same_request_replays_when_competitor_deletes_before_phase(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    phase: PurgeRacePhase,
) -> None:
    await assert_same_request_replays_after_competing_delete(phase, monkeypatch, client)


async def test_prepare_rechecks_for_completed_receipt_after_lock(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await assert_prepare_rechecks_after_locked_delete(monkeypatch, client)


async def test_snapshot_rechecks_after_competing_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await assert_snapshot_rechecks_after_competing_materialization(monkeypatch)


async def test_snapshot_rejects_tampered_preexisting_complete_manifest() -> None:
    await grant_test_org_admin()
    history = await seed_focused_purge_history()
    async with TestSession() as db:
        actor = await db.get(User, TEST_USER_ID)
        assert actor is not None
        request = ConversationHistoryMaterializationRequest(
            db,
            CurrentUser(id=actor.id, email=actor.email, name=actor.name),
            history.agent_id,
            history.conversation_id,
            "History purge",
            history.saver,
        )
        manifest = await materialize_conversation_history(request)
        await db.commit()
        persisted = await db.get(ConversationHistoryManifest, manifest.id)
        assert persisted is not None
        persisted.canonical_payload_sha256 = "0" * 64
        await db.commit()

        with pytest.raises(HistorySnapshotIncompleteError) as error:
            await materialize_conversation_history(request)

    assert error.value.code == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"


async def test_purge_commits_each_manifest_before_next_projection(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await grant_test_org_admin()
    history = await seed_focused_purge_history()
    await _add_second_purge_conversation(history)
    archived = await client.delete(f"/api/agents/{history.agent_id}")
    assert archived.status_code == 204
    cleanup_saver = RecordingSaver()
    monkeypatch.setattr(agent_purge_cleanup.checkpointer, "get_checkpointer", lambda: cleanup_saver)
    original_materialize = agent_purge_service.materialize_conversation_history
    original_commit = AsyncSession.commit
    materialized = 0
    commits = 0

    async with TestSession() as db:

        async def tracked_commit(session: AsyncSession) -> None:
            nonlocal commits
            await original_commit(session)
            if session is db:
                commits += 1

        async def observe_boundary(
            request: ConversationHistoryMaterializationRequest,
        ) -> ConversationHistoryManifest:
            nonlocal materialized
            if materialized == 1:
                assert commits == 2
            manifest = await original_materialize(request)
            materialized += 1
            return manifest

        monkeypatch.setattr(AsyncSession, "commit", tracked_commit)
        monkeypatch.setattr(
            agent_purge_service, "materialize_conversation_history", observe_boundary
        )
        actor = await db.get(User, TEST_USER_ID)
        assert actor is not None
        receipt = await purge_agent(
            AgentPurgeCommand(
                db,
                CurrentUser(id=actor.id, email=actor.email, name=actor.name),
                history.agent_id,
                "History purge",
                history.saver,
            )
        )

    assert materialized == 2
    assert receipt.state == "completed"


async def test_second_snapshot_failure_preserves_preparing_partial_state(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await grant_test_org_admin()
    history = await seed_focused_purge_history()
    await _add_second_purge_conversation(history)
    archived = await client.delete(f"/api/agents/{history.agent_id}")
    assert archived.status_code == 204
    original_materialize = agent_purge_service.materialize_conversation_history
    materialized = 0

    async def fail_second(
        request: ConversationHistoryMaterializationRequest,
    ) -> ConversationHistoryManifest:
        nonlocal materialized
        if materialized == 1:
            raise HistorySnapshotIncompleteError("second snapshot failed")
        manifest = await original_materialize(request)
        materialized += 1
        return manifest

    monkeypatch.setattr(agent_purge_service, "materialize_conversation_history", fail_second)
    async with TestSession() as db:
        actor = await db.get(User, TEST_USER_ID)
        assert actor is not None
        with pytest.raises(HistorySnapshotIncompleteError):
            await purge_agent(
                AgentPurgeCommand(
                    db,
                    CurrentUser(id=actor.id, email=actor.email, name=actor.name),
                    history.agent_id,
                    "History purge",
                    history.saver,
                )
            )

    async with TestSession() as verification_db:
        receipt = await verification_db.scalar(
            select(AgentPurgeReceipt).where(AgentPurgeReceipt.agent_id_snapshot == history.agent_id)
        )
        manifests = await verification_db.scalar(
            select(func.count())
            .select_from(ConversationHistoryManifest)
            .where(ConversationHistoryManifest.source_agent_id_snapshot == history.agent_id)
        )
        cleanup_items = await verification_db.scalar(
            select(func.count()).select_from(AgentPurgeCleanupItem)
        )
        conversations = tuple(
            await verification_db.scalars(
                select(Conversation).where(Conversation.agent_id == history.agent_id)
            )
        )
        assert receipt is not None and receipt.state == "preparing"
        assert manifests == 1 and cleanup_items == 0
        assert await verification_db.get(Agent, history.agent_id) is not None
        assert len(conversations) == 2


async def test_native_race_gate_only_blocks_first_snapshot_per_request() -> None:
    gate = PurgeRaceGate()
    calls = [0, 0]

    async def exercise(index: int) -> None:
        gate.bind(index)
        try:
            for _ in range(3):
                await gate.wait()
                calls[index] += 1
        finally:
            gate.finish(index)

    with fail_after(1):
        async with create_task_group() as tasks:
            tasks.start_soon(exercise, 0)
            tasks.start_soon(exercise, 1)

    assert calls == [3, 3]
