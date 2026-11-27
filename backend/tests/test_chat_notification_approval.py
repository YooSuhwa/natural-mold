"""A waiting conversation goal delivers the ordinary tool approval to its decider once."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import recording, service
from app.config import settings
from app.models.approval_request import ApprovalRequest
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_goal import ConversationGoal, GoalAttempt, GoalTransitionIntent
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.notification import Notification
from app.models.organization import OrganizationMember
from app.services.chat_notification_outbox import drain_chat_notification_outbox
from app.services.conversation_goal_service import block_goal
from tests.authz.g03_support import auth_payload
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, make_user
from tests.integration._seed import seed_conversation_with_agent

INTERRUPT = "goal-approval"
PRIVATE_DETAIL = "private-fixture-value-that-must-not-survive-restart"


def _payload(name: str = "send_mail") -> dict[str, Any]:
    return {
        "action_requests": [
            {"name": name, "args": {"body": PRIVATE_DETAIL}, "description": PRIVATE_DETAIL}
        ],
        "review_configs": [{"action_name": name, "allowed_decisions": ["approve", "reject"]}],
    }


@pytest.fixture
async def waiting_goal(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[ConversationGoal, ConversationRun]:
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    cid = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, cid)
    assert conversation is not None
    interrupted_at = utc_now_naive() - timedelta(minutes=2)
    run = ConversationRun(
        user_id=TEST_USER_ID,
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
        conversation_id=cid,
        agent_id=conversation.agent_id,
        source="chat",
        run_source="CHAT",
        status="interrupted",
        interrupt_id=INTERRUPT,
        completed_at=interrupted_at,
        updated_at=interrupted_at,
    )
    goal = ConversationGoal(
        user_id=TEST_USER_ID,
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
        conversation_id=cid,
        title="Confirm work",
        criteria=[],
    )
    db.add_all([goal, run])
    await db.commit()
    return goal, run


async def _wait_for_approval(
    db: AsyncSession, goal: ConversationGoal, run: ConversationRun
) -> None:
    attempt = await db.scalar(
        select(GoalAttempt).where(GoalAttempt.goal_id == goal.id, GoalAttempt.run_id == run.id)
    )
    if attempt is None:
        attempt = GoalAttempt(
            goal_id=goal.id, goal_version=goal.version, run_id=run.id, steer_seq=0
        )
        db.add(attempt)
    await block_goal(db, goal, "approval_pending", attempt=attempt, interrupt_id=run.interrupt_id)
    await db.commit()


async def _record(db: AsyncSession, run: ConversationRun) -> ApprovalRequest:
    [request] = await service.record_pending(
        db,
        run,
        [service.PendingInterrupt(INTERRUPT, _payload())],
        origin=service.RunOrigin("Test agent"),
        secret_values=[PRIVATE_DETAIL],
    )
    await db.commit()
    return request


async def _drain(db: AsyncSession) -> None:
    await drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID)
    await db.commit()


@pytest.mark.parametrize("prior_bell", [False, True])
async def test_pending_request_uses_its_canonical_notice_across_goal_revisions(
    db: AsyncSession, waiting_goal: tuple[ConversationGoal, ConversationRun], prior_bell: bool
) -> None:
    goal, run = waiting_goal
    request = await _record(db, run)
    old_bell_id = None
    if prior_bell:
        await recording.notify_requested(db, [request])
        bell = (await db.scalars(select(Notification))).one()
        bell.created_at = datetime.now(UTC) - timedelta(hours=2)
        bell.read_at = datetime.now(UTC) - timedelta(minutes=10)
        old_bell_id = bell.id
        await db.commit()

    await _wait_for_approval(db, goal, run)
    await _drain(db)
    bell = (await db.scalars(select(Notification))).one()
    assert (bell.kind, bell.target_type, bell.target_id, bell.actor_id) == (
        "approval.requested",
        "approval_request",
        request.id,
        None,
    )
    assert (bell.tenant_id, bell.org_id, bell.user_id, bell.link) == (
        TEST_TENANT_ID,
        TEST_ORG_ID,
        TEST_USER_ID,
        "/approvals",
    )
    assert bell.payload.get("chat_goal") is True
    assert "target_name" not in bell.payload
    if prior_bell:
        assert bell.id == old_bell_id and bell.read_at is not None
    receipt = (await db.scalars(select(ChatNotificationOutbox))).one()
    assert (receipt.status, receipt.approval_request_id, receipt.notification_id) == (
        "delivered",
        request.id,
        bell.id,
    )
    await db.delete(bell)
    goal.status = "active"
    goal.revision += 1
    await _wait_for_approval(db, goal, run)
    await _drain(db)
    assert not await db.scalar(select(Notification.id))
    assert list(await db.scalars(select(ApprovalRequest.id))) == [request.id]


@pytest.mark.parametrize(
    "reason",
    ["revoked", "expired", "decided", "other_decider", "stale_goal", "wrong_interrupt", "resumed"],
)
async def test_ineligible_transition_never_notifies(
    db: AsyncSession, waiting_goal: tuple[ConversationGoal, ConversationRun], reason: str
) -> None:
    goal, run = waiting_goal
    request = await _record(db, run)
    await _wait_for_approval(db, goal, run)
    if reason == "revoked":
        member = await db.get(OrganizationMember, (TEST_ORG_ID, TEST_USER_ID))
        assert member is not None
        member.status = "removed"
    elif reason == "expired":
        request.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif reason == "decided":
        request.status = "rejected"
    elif reason == "other_decider":
        request.decider_user_id = (await make_user(db)).id
    elif reason == "stale_goal":
        goal.revision += 1
    elif reason == "resumed":
        db.add(
            ConversationRun(
                parent_run_id=run.id,
                conversation_id=run.conversation_id,
                tenant_id=run.tenant_id,
                org_id=run.org_id,
                user_id=run.user_id,
                agent_id=run.agent_id,
                source="resume",
                status="completed",
            )
        )
    else:
        run.interrupt_id = "different-interrupt"
    await db.commit()
    await _drain(db)
    assert not await db.scalar(select(Notification.id))
    assert (await db.scalars(select(ChatNotificationOutbox.status))).one() == "suppressed"


@pytest.mark.parametrize("question", ["ask_user", "auth_required"])
async def test_late_confirmation_of_an_input_delivers_its_blocked_notice_after_switch_off(
    db: AsyncSession,
    waiting_goal: tuple[ConversationGoal, ConversationRun],
    monkeypatch: pytest.MonkeyPatch,
    question: str,
) -> None:
    goal, run = waiting_goal
    await _wait_for_approval(db, goal, run)
    original = (await db.scalars(select(GoalTransitionIntent))).one()
    revision = goal.revision
    assert original.kind == "approval_requested" and goal.blocked_reason == "approval_pending"
    payload = _payload("ask_user") if question == "ask_user" else auth_payload()
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", False)
    monkeypatch.setattr(
        "app.services.chat_notification_approval.interrupt_payloads_from_checkpointer",
        AsyncMock(return_value=[{"interrupt_id": INTERRUPT, "payload": payload, "namespace": []}]),
    )
    for _ in range(3):
        await _drain(db)
    await db.refresh(goal)
    assert (goal.status, goal.blocked_reason, goal.revision) == (
        "blocked",
        "input_required",
        revision + 1,
    )
    intents = (
        await db.scalars(select(GoalTransitionIntent).order_by(GoalTransitionIntent.goal_revision))
    ).all()
    assert [(t.run_id, t.goal_revision, t.kind) for t in intents] == [
        (run.id, revision, "approval_requested"),
        (run.id, revision + 1, "blocked"),
    ]
    assert (
        intents[0].id == original.id and intents[0].payload["blocked_reason"] == "approval_pending"
    )
    assert intents[1].payload["blocked_reason"] == "input_required"
    [notice] = (await db.scalars(select(Notification))).all()
    assert notice.kind == "chat_goal.blocked"
    assert PRIVATE_DETAIL not in json.dumps(notice.payload)
    assert await db.scalar(select(ApprovalRequest.id)) is None


async def test_deferred_checkpoint_recovers_without_private_arguments(
    db: AsyncSession,
    waiting_goal: tuple[ConversationGoal, ConversationRun],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goal, run = waiting_goal
    await _wait_for_approval(db, goal, run)
    checkpoint = AsyncMock(
        side_effect=[[], [{"interrupt_id": INTERRUPT, "payload": _payload(), "namespace": []}]]
    )
    monkeypatch.setattr(
        "app.services.chat_notification_approval.interrupt_payloads_from_checkpointer", checkpoint
    )
    await _drain(db)
    receipt = (await db.scalars(select(ChatNotificationOutbox))).one()
    assert (receipt.status, receipt.attempt_count, receipt.notification_id) == ("pending", 1, None)
    assert not await db.scalar(select(ApprovalRequest.id))

    await db.execute(
        update(ChatNotificationOutbox).values(available_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    await _drain(db)
    request = (await db.scalars(select(ApprovalRequest))).one()
    assert (request.run_id, request.interrupt_id, request.source, request.status) == (
        run.id,
        INTERRUPT,
        "chat",
        "pending",
    )
    assert request.args_digest == service.args_digest(_payload())
    assert PRIVATE_DETAIL not in json.dumps(request.actions)
    assert request.actions[0]["allowed_decisions"] == ["reject"]
    notice = (await db.scalars(select(Notification))).one()
    assert (notice.target_id, notice.kind) == (request.id, "approval.requested")
    assert PRIVATE_DETAIL not in json.dumps(notice.payload)
    await _drain(db)
    assert list(await db.scalars(select(ApprovalRequest.id))) == [request.id]
    assert list(await db.scalars(select(Notification.id))) == [notice.id]
