"""Deterministic same-request interleavings for focused Agent purge tests."""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_purge import AgentPurgeReceipt
from app.models.audit_event import AuditEvent
from app.models.conversation_history import ConversationHistoryManifest
from app.models.user import User
from app.services import (
    agent_purge_cleanup,
    agent_purge_preparation,
    agent_purge_service,
    conversation_history_snapshot,
)
from app.services.agent_purge_preparation import prepare_agent_purge
from app.services.agent_purge_service import purge_agent
from app.services.agent_purge_types import AgentPurgeCommand, PreparedAgentPurge
from app.services.conversation_history_materializer import HistoryProjectionRequest
from app.services.conversation_history_types import (
    ConversationHistoryMaterializationRequest,
    HistoryProjection,
)
from tests.agent_purge_cleanup_cases import RecordingSaver
from tests.agent_purge_service_cases import grant_test_org_admin, seed_focused_purge_history
from tests.conftest import TEST_USER_ID, TestSession, seed_agent

type PurgeRacePhase = Literal["snapshot", "finalize"]


async def assert_snapshot_rechecks_after_competing_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resume a stale manifest reader after a competing request commits."""
    await grant_test_org_admin()
    history = await seed_focused_purge_history()
    original_projection = conversation_history_snapshot.build_history_projection
    competed = False
    competing_id: UUID | None = None

    async def interleave_projection(request: HistoryProjectionRequest) -> HistoryProjection:
        nonlocal competed, competing_id
        projection = await original_projection(request)
        if competed:
            return projection
        competed = True
        await request.request.db.commit()
        async with TestSession() as competing_db:
            actor = await competing_db.get(User, TEST_USER_ID)
            assert actor is not None
            competing = await conversation_history_snapshot.materialize_conversation_history(
                ConversationHistoryMaterializationRequest(
                    competing_db,
                    CurrentUser(id=actor.id, email=actor.email, name=actor.name),
                    history.agent_id,
                    history.conversation_id,
                    "History purge",
                    history.saver,
                )
            )
            await competing_db.commit()
        assert competing.state == "complete"
        competing_id = competing.id
        return projection

    monkeypatch.setattr(
        conversation_history_snapshot,
        "build_history_projection",
        interleave_projection,
    )
    async with TestSession() as db:
        actor = await db.get(User, TEST_USER_ID)
        assert actor is not None
        manifest = await conversation_history_snapshot.materialize_conversation_history(
            ConversationHistoryMaterializationRequest(
                db,
                CurrentUser(id=actor.id, email=actor.email, name=actor.name),
                history.agent_id,
                history.conversation_id,
                "History purge",
                history.saver,
            )
        )
        await db.commit()
        count = await db.scalar(
            select(func.count())
            .select_from(ConversationHistoryManifest)
            .where(ConversationHistoryManifest.conversation_id == history.conversation_id)
        )
    assert competed is True
    assert manifest.id == competing_id
    assert count == 1


async def assert_same_request_replays_after_competing_delete(
    phase: PurgeRacePhase,
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
) -> None:
    """Delete through request two immediately before request one's selected phase."""
    await grant_test_org_admin()
    history = await seed_focused_purge_history()
    archived = await client.delete(f"/api/agents/{history.agent_id}")
    assert archived.status_code == 204
    cleanup_saver = RecordingSaver()
    monkeypatch.setattr(agent_purge_cleanup.checkpointer, "get_checkpointer", lambda: cleanup_saver)
    competed = False

    async def command_for_new_session() -> tuple[AgentPurgeCommand, AsyncSession]:
        db = TestSession()
        actor = await db.get(User, TEST_USER_ID)
        assert actor is not None
        return (
            AgentPurgeCommand(
                db,
                CurrentUser(id=actor.id, email=actor.email, name=actor.name),
                history.agent_id,
                "History purge",
                history.saver,
            ),
            db,
        )

    async def run_competing_request() -> None:
        command, db = await command_for_new_session()
        try:
            await purge_agent(command)
        finally:
            await db.close()

    if phase == "snapshot":
        original_snapshot = agent_purge_service.materialize_conversation_history

        async def delete_before_snapshot(
            request: ConversationHistoryMaterializationRequest,
        ) -> ConversationHistoryManifest:
            nonlocal competed
            if not competed:
                competed = True
                await run_competing_request()
            return await original_snapshot(request)

        monkeypatch.setattr(
            agent_purge_service,
            "materialize_conversation_history",
            delete_before_snapshot,
        )
    else:
        original_finalize = agent_purge_service.finalize_agent_purge

        async def delete_before_finalize(
            command: AgentPurgeCommand,
            prepared: PreparedAgentPurge,
        ) -> None:
            nonlocal competed
            if not competed:
                competed = True
                await run_competing_request()
            await original_finalize(command, prepared)

        monkeypatch.setattr(agent_purge_service, "finalize_agent_purge", delete_before_finalize)

    command, db = await command_for_new_session()
    try:
        receipt = await purge_agent(command)
    finally:
        await db.close()

    assert receipt.state == "completed"
    async with TestSession() as verification_db:
        assert (
            await verification_db.scalar(
                select(func.count())
                .select_from(AgentPurgeReceipt)
                .where(AgentPurgeReceipt.agent_id_snapshot == history.agent_id)
            )
            == 1
        )
        assert (
            await verification_db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action == "agent.purge",
                    AuditEvent.target_id == str(history.agent_id),
                )
            )
            == 1
        )
    assert cleanup_saver.deleted_threads == [str(history.conversation_id)]


async def assert_prepare_rechecks_after_locked_delete(
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
) -> None:
    """Make the Agent disappear at the lifecycle-lock boundary and require replay."""
    await grant_test_org_admin()
    async with TestSession() as db:
        _, _, agent = await seed_agent(db, name="Prepare boundary")
        await db.commit()
        agent_id = agent.id
    archived = await client.delete(f"/api/agents/{agent_id}")
    assert archived.status_code == 204
    original_lock = agent_purge_preparation.lock_lifecycle_target

    async def delete_at_lock(db: AsyncSession, locked_agent_id: UUID) -> None:
        await original_lock(db, locked_agent_id)
        target = (
            await db.execute(
                select(Agent.tenant_id, Agent.org_id, Agent.user_id, Agent.name).where(
                    Agent.id == locked_agent_id
                )
            )
        ).one()
        receipt = AgentPurgeReceipt(
            id=uuid4(),
            tenant_id=target.tenant_id,
            org_id=target.org_id,
            agent_id_snapshot=locked_agent_id,
            owner_user_id_snapshot=target.user_id,
            agent_name_snapshot=target.name,
            requester_user_id=TEST_USER_ID,
            state="completed",
        )
        db.add(receipt)
        await db.execute(delete(Agent).where(Agent.id == locked_agent_id))
        await db.flush()

    monkeypatch.setattr(agent_purge_preparation, "lock_lifecycle_target", delete_at_lock)
    async with TestSession() as db:
        actor = await db.get(User, TEST_USER_ID)
        assert actor is not None
        prepared = await prepare_agent_purge(
            AgentPurgeCommand(
                db,
                CurrentUser(id=actor.id, email=actor.email, name=actor.name),
                agent_id,
                "Prepare boundary",
            )
        )
        assert prepared.database_deleted is True
        assert prepared.conversation_ids == ()


__all__ = [
    "PurgeRacePhase",
    "assert_prepare_rechecks_after_locked_delete",
    "assert_snapshot_rechecks_after_competing_materialization",
    "assert_same_request_replays_after_competing_delete",
]
