"""A revoked queue dispatcher keeps owner instructions and stops reserved goal work."""

from dataclasses import dataclass, replace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime as authz_runtime
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.run_decisions import fresh_run_decision_scope
from app.config import settings
from app.exceptions import ForbiddenError
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_goal import ConversationGoal, GoalAttempt, GoalTransitionIntent
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_steer import ConversationRunSteer
from app.services import conversation_run_queue_blocked, conversation_run_service
from app.services.conversation_goal_worker import reconcile_goal_run, stop_reservations_on_denial
from app.services.conversation_run_queue_worker import dispatch_next_for_conversation
from app.services.conversation_run_steer_rollover import reconcile_terminal_steers
from tests.authz.test_conversation_route_authz import _service
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.sqlite_test_engine import TestSession
from tests.test_conversation_goal_worker import _accept

OWNER = unit_context(TEST_USER_ID)


@dataclass(frozen=True)
class Waiting:
    conversation: UUID
    agent: UUID
    goal_run: UUID
    steer_run: UUID
    steer: UUID


@pytest.fixture
async def waiting(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> Waiting:
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({OWNER.org_id}))
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    monkeypatch.setattr(settings, "chat_steer_enabled", True)
    room = await db.get(Conversation, await seed_conversation_with_agent())
    assert room is not None and room.agent_id is not None
    original = await _accept(db, room)
    await conversation_run_service.transition_run(db, original, "running", worker_instance_id="old")
    await db.commit()
    receipt = await client.post(
        f"/api/conversations/{room.id}/runs/{original.id}/steers",
        json={"client_request_id": str(uuid4()), "text": "Include prices."},
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "pending"
    await conversation_run_service.transition_run(db, original, "completed")
    await db.commit()

    # The old worker stopped between its terminal commit and its steer rollover.
    # A new goal starts at that existing receipt's watermark.
    created = await client.put(
        f"/api/conversations/{room.id}/goal",
        json={
            "expected_revision": 0,
            "title": "Supplier report",
            "criteria": [{"kind": "nonempty_file", "logical_path": "report.txt"}],
        },
    )
    assert created.status_code == 200, created.text
    run = await _accept(db, room)
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="new")
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()
    await reconcile_goal_run(db, run.id, room.id)
    await db.commit()
    await reconcile_terminal_steers(db, conversation_id=room.id, run_id=original.id, context=OWNER)
    await db.commit()
    return Waiting(room.id, room.agent_id, run.id, original.id, UUID(receipt.json()["id"]))


async def assert_queue_state(waiting: Waiting, *, denied: bool) -> None:
    async with TestSession() as db:
        room = await db.get(Conversation, waiting.conversation)
        goal = (await db.scalars(select(ConversationGoal))).one()
        attempt = (await db.scalars(select(GoalAttempt))).one()
        receipt = await db.get(ConversationRunSteer, waiting.steer)
        inputs = list(await db.scalars(select(ConversationRunInput)))
        assert room is not None and room.queue_paused is denied
        assert receipt is not None and receipt.status == "queued" and receipt.applied_at is None
        assert len(inputs) == 2
        assert all(row.run_id is None for row in inputs)
        assert {(row.source, row.status, row.id) for row in inputs} == {
            ("steer", "pending", receipt.queued_input_id),
            ("goal_followup", "canceled" if denied else "pending", attempt.followup_input_id),
        }
        assert (goal.status, goal.blocked_reason) == (
            ("blocked", "permission_revoked") if denied else ("active", None)
        )
        assert goal.last_evidence == attempt.evidence
        assert goal.last_evidence is not None and goal.last_evidence["checks"]
        assert attempt.status == ("blocked" if denied else "followup_reserved")
        assert set(await db.scalars(select(ConversationRun.id))) == {
            waiting.steer_run,
            waiting.goal_run,
        }
        intents = list(await db.scalars(select(GoalTransitionIntent)))
        notifications = list(await db.scalars(select(ChatNotificationOutbox)))
        assert len(intents) == len(notifications) == int(denied)
        if denied:
            assert (intents[0].kind, intents[0].run_id) == ("blocked", waiting.goal_run)
            assert intents[0].payload["evidence"] is None
            assert (
                notifications[0].kind,
                notifications[0].source_id,
                notifications[0].user_id,
            ) == (
                "chat_goal.blocked",
                intents[0].id,
                OWNER.user_id,
            )


async def test_denied_dispatch_rolls_back_then_stops_only_the_goal(
    waiting: Waiting, monkeypatch: pytest.MonkeyPatch
) -> None:
    await assert_queue_state(waiting, denied=False)
    grant = (f"user:{OWNER.user_id}", "can_run", f"agent:{waiting.agent}")
    monkeypatch.setattr(authz_runtime, "_current", _service("enforce", {grant}))
    with fresh_run_decision_scope() as parent:
        await require_agent_run(OWNER, waiting.agent)
        assert parent.answers
        monkeypatch.setattr(authz_runtime, "_current", _service("enforce"))
        with monkeypatch.context() as fail:
            fail.setattr(
                conversation_run_queue_blocked,
                "record_run_blocked",
                AsyncMock(side_effect=RuntimeError("audit unavailable")),
            )
            with pytest.raises(RuntimeError, match="audit unavailable"):
                await dispatch_next_for_conversation(waiting.conversation)
        await assert_queue_state(waiting, denied=False)
        for _ in range(2):
            assert await dispatch_next_for_conversation(waiting.conversation) is None
            await assert_queue_state(waiting, denied=True)


@pytest.mark.parametrize("with_proof", [False, True])
async def test_mismatched_subject_cannot_stop_owned_work(
    waiting: Waiting, with_proof: bool
) -> None:
    scopes = (
        replace(OWNER, user_id=uuid4()),
        replace(OWNER, org_id=uuid4()),
        replace(OWNER, tenant_id=uuid4()),
    )
    for scope in scopes:
        async with TestSession() as db:
            if with_proof:
                await acquire_execution_proof(
                    db, OWNER, root_agent_id=waiting.agent, conversation_ids=(waiting.conversation,)
                )
            stopped = await stop_reservations_on_denial(
                db, waiting.conversation, scope, ForbiddenError("permission_revoked", "revoked")
            )
            assert not stopped
            await db.commit()
    await assert_queue_state(waiting, denied=False)
