"""Exercise the ordinary-run lifecycle, including the durable gap before goal evaluation."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.database import SessionTenant
from app.exceptions import ForbiddenError
from app.models.conversation import Conversation
from app.models.conversation_goal import ConversationGoal, GoalAttempt
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_goal_service as goals
from app.services import conversation_goal_worker as worker
from app.services import conversation_run_queue_service as queue
from app.services import conversation_run_service as runs
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent


async def _empty_steers(*_args):
    # F02 has its own independent migration, tested together at integration.
    return goals.GoalSteerState(0, 0, False)


async def _seed(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    conversation = await db.get(Conversation, await seed_conversation_with_agent())
    assert conversation is not None
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({conversation.org_id}))
    monkeypatch.setattr(goals, "_watermarks", _empty_steers)
    goal = ConversationGoal(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        tenant_id=conversation.tenant_id,
        org_id=conversation.org_id,
        title="Create report",
        criteria=[{"kind": "nonempty_file", "logical_path": "report.txt"}],
        status="active",
        version=1,
        revision=1,
        latest_steer_seq=0,
        auto_followups=0,
        no_progress_count=0,
    )
    db.add(goal)
    await db.commit()
    return conversation, goal


async def _accept(db, conversation):
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation.id, conversation.agent_id, unit_context(TEST_USER_ID), "chat", "start"
        ),
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    await db.commit()
    return run


@pytest.mark.asyncio
async def test_draft_first_run_reconciliation_is_idempotent_and_can_be_recovered(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, goal = await _seed(db, monkeypatch)
    conversation.source = "draft"
    await db.commit()
    run = await _accept(db, conversation)
    assert (run.metadata_json or {}).get(worker.ATTEMPT_KEY)
    await runs.transition_run(db, run, "running", worker_instance_id="goal-worker")
    await runs.transition_run(db, run, "completed")
    conversation.source = "ui"
    await db.commit()
    # The original process stopped here. Recovery evaluates the durable terminal run.
    await worker.reconcile_goal_run(db, run.id, conversation.id)
    await db.commit()
    await worker.reconcile_goal_run(db, run.id, conversation.id)
    await db.commit()
    attempt = (await db.scalars(select(GoalAttempt).where(GoalAttempt.run_id == run.id))).one()
    assert attempt.status == "followup_reserved"
    items = (
        await db.scalars(
            select(ConversationRunInput).where(
                ConversationRunInput.source == worker.GOAL_INPUT_SOURCE
            )
        )
    ).all()
    assert len(items) == 1
    assert items[0].id == attempt.followup_input_id
    assert goal.auto_followups == 1
    claimed = await queue.claim_next_input(
        db, conversation_id=conversation.id, context=unit_context(TEST_USER_ID)
    )
    assert claimed is not None and claimed.input.id == items[0].id
    assert (claimed.run.metadata_json or {})[worker.INPUT_KEY] == str(items[0].id)
    await db.commit()
    # User pauses the goal after the queue claim was committed, before worker start.
    goal.status = "paused"
    await db.commit()
    assert not await worker.automatic_run_can_start(db, claimed.run, unit_context(TEST_USER_ID))
    assert claimed.run.status == "canceling"


@pytest.mark.asyncio
async def test_manual_input_wins_and_supersedes_a_reserved_automatic_run(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, _goal = await _seed(db, monkeypatch)
    run = await _accept(db, conversation)
    await runs.transition_run(db, run, "running", worker_instance_id="goal-worker")
    await runs.transition_run(db, run, "completed")
    await db.commit()
    await worker.reconcile_goal_run(db, run.id, conversation.id)
    await db.commit()
    item = await queue.enqueue_input(
        db,
        conversation_id=conversation.id,
        context=unit_context(TEST_USER_ID),
        client_request_id=str(uuid4()),
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "I have a correction"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    claimed = await queue.claim_next_input(
        db, conversation_id=conversation.id, context=unit_context(TEST_USER_ID)
    )
    assert claimed is not None and claimed.input.id == item.id
    await db.commit()
    older = (
        await db.scalars(
            select(ConversationRunInput).where(
                ConversationRunInput.source == worker.GOAL_INPUT_SOURCE
            )
        )
    ).one()
    assert older.status == "canceled"
    assert not (claimed.run.metadata_json or {}).get(worker.INPUT_KEY)


@pytest.mark.asyncio
async def test_revoked_permission_cancels_reservation_before_queue_can_claim(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import conversation_run_queue_claim as claim

    conversation, goal = await _seed(db, monkeypatch)
    run = await _accept(db, conversation)
    await runs.transition_run(db, run, "running", worker_instance_id="goal-worker")
    await runs.transition_run(db, run, "completed")
    await db.commit()
    await worker.reconcile_goal_run(db, run.id, conversation.id)
    await db.commit()

    async def deny(_context, _agent_id):
        return ForbiddenError("permission_revoked", "Permission revoked")

    with monkeypatch.context() as patch:
        patch.setattr(claim, "queue_dispatch_denial", deny)
        assert (
            await queue.claim_next_input(
                db, conversation_id=conversation.id, context=unit_context(TEST_USER_ID)
            )
            is None
        )
    assert goal.status == "blocked"
    assert goal.blocked_reason == "permission_revoked"
    row = (
        await db.scalars(
            select(ConversationRunInput).where(
                ConversationRunInput.source == worker.GOAL_INPUT_SOURCE
            )
        )
    ).one()
    assert row.status == "canceled"
    assert (
        await queue.claim_next_input(
            db, conversation_id=conversation.id, context=unit_context(TEST_USER_ID)
        )
        is None
    )


@pytest.mark.asyncio
async def test_queue_denial_stops_only_its_own_tenant_scoped_reservation(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, goal = await _seed(db, monkeypatch)
    run = await _accept(db, conversation)
    await runs.transition_run(db, run, "running", worker_instance_id="goal-worker")
    await runs.transition_run(db, run, "completed")
    await db.commit()
    await worker.reconcile_goal_run(db, run.id, conversation.id)
    await db.commit()
    item = (
        await db.scalars(
            select(ConversationRunInput).where(
                ConversationRunInput.conversation_id == conversation.id,
                ConversationRunInput.source == worker.GOAL_INPUT_SOURCE,
            )
        )
    ).one()
    error = ForbiddenError("permission_revoked", "Permission revoked")
    for wrong_scope in (
        SessionTenant(uuid4(), conversation.user_id),
        SessionTenant(conversation.tenant_id),
        SessionTenant(conversation.tenant_id, uuid4()),
        OrgContext(uuid4(), conversation.tenant_id, conversation.user_id),
    ):
        await worker.stop_reservations_on_denial(db, conversation.id, wrong_scope, error)
        assert (goal.status, item.status) == ("active", "pending")

    # The queue worker still has its recorded private company/owner after membership loss.
    await worker.stop_reservations_on_denial(
        db, conversation.id, SessionTenant(conversation.tenant_id, conversation.user_id), error
    )
    assert (goal.status, goal.blocked_reason, item.status) == (
        "blocked",
        "permission_revoked",
        "canceled",
    )


@pytest.mark.asyncio
async def test_queue_recovery_discovers_terminal_goal_with_no_user_inputs(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import conversation_run_queue_worker
    from tests.conftest import TestSession

    conversation, goal = await _seed(db, monkeypatch)
    run = await _accept(db, conversation)
    await runs.transition_run(db, run, "running", worker_instance_id="goal-worker")
    await runs.transition_run(db, run, "completed")
    await db.commit()
    monkeypatch.setattr(conversation_run_queue_worker, "async_session", TestSession)
    await worker.recover_goal_attempts()
    await worker.recover_goal_attempts()
    await db.refresh(goal)
    assert goal.auto_followups == 1
    rows = (await db.scalars(select(ConversationRunInput))).all()
    assert len(rows) == 1 and rows[0].source == worker.GOAL_INPUT_SOURCE


@pytest.mark.asyncio
async def test_approval_resume_inherits_the_automatic_tools_restriction(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, goal = await _seed(db, monkeypatch)
    context = unit_context(TEST_USER_ID)
    run = await _accept(db, conversation)
    await runs.transition_run(db, run, "running", worker_instance_id="root")
    await runs.transition_run(db, run, "completed")
    await db.commit()
    await worker.reconcile_goal_run(db, run.id, conversation.id)
    await db.commit()
    claimed = await queue.claim_next_input(db, conversation_id=conversation.id, context=context)
    assert claimed is not None
    followup = claimed.run
    await db.commit()
    assert await worker.automatic_run_can_start(db, followup, context)
    await runs.transition_run(db, followup, "running", worker_instance_id="automatic")
    await runs.transition_run(db, followup, "interrupted", interrupt_id="approval")
    await db.commit()
    await worker.reconcile_goal_run(db, followup.id, conversation.id)
    await db.commit()
    assert (goal.status, goal.blocked_reason) == ("blocked", "approval_pending")
    assert conversation.agent_id is not None
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation.id,
            conversation.agent_id,
            context,
            "chat",
            None,
            run_source="resume",
            parent_run_id=followup.id,
            interrupt_id="approval",
        ),
    )
    await db.commit()
    resumed = await db.get(ConversationRun, accepted.run_id)
    assert resumed is not None
    assert (resumed.metadata_json or {}).get(worker.INPUT_KEY) == str(claimed.input.id)
    assert await worker.automatic_run_can_start(db, resumed, context)
