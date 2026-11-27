from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TypedDict

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.service import PendingInterrupt, RunOrigin, record_pending
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.authz.run_decisions import fresh_run_decision_scope
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput, JsonValue
from app.models.conversation_run_steer import ConversationRunSteer
from app.models.organization import Organization, OrganizationMember
from app.schemas.conversation_run_steer import ConversationRunSteerCreate
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.conversation_run_steers import (
    admit_steer,
    claim_steers_for_model,
    confirm_applied_steers,
    get_conversation_steer_watermarks,
    list_steers,
)
from tests.authz.authz_service_testkit import RecordingClient, service
from tests.authz.unit_scope_fixture import ensure_unit_scope, unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent

OWNER = unit_context(TEST_USER_ID)


class _RunTarget(TypedDict):
    conversation_id: uuid.UUID
    run_id: uuid.UUID


def _assert_conflict(exc: HTTPException, code: str) -> None:
    assert exc.status_code == 409
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("code") == code


async def _running(db: AsyncSession, conversation_id: uuid.UUID | None = None) -> ConversationRun:
    if conversation_id is None:
        conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conversation),
        context=OWNER,
        source="chat",
        input_preview="original request",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="worker")
    await db.commit()
    return run


async def _admit(
    db: AsyncSession,
    run: ConversationRun,
    text: str,
    *,
    request_id: uuid.UUID | None = None,
    context: OrgContext = OWNER,
) -> ConversationRunSteer:
    return await admit_steer(
        db,
        conversation_id=run.conversation_id,
        run_id=run.id,
        context=context,
        client_request_id=request_id or uuid.uuid4(),
        text=text,
    )


async def test_same_request_survives_reconnect_and_terminal_retry(db: AsyncSession) -> None:
    run = await _running(db)
    target: _RunTarget = {"conversation_id": run.conversation_id, "run_id": run.id}
    request_id = uuid.uuid4()
    first = await _admit(db, run, "한국 사례만", request_id=request_id)
    await db.commit()
    retried = await _admit(db, run, "한국 사례만", request_id=request_id)
    second = await _admit(db, run, "공공기관 우선")
    await db.commit()

    assert [first.id, first.sequence, first.status] == [retried.id, 1, "pending"]
    assert second.sequence == 2
    assert (first.org_id, first.tenant_id, first.user_id) == (
        OWNER.org_id,
        OWNER.tenant_id,
        OWNER.user_id,
    )
    assert [s.id for s in await list_steers(db, **target, context=OWNER)] == [first.id, second.id]
    assert run.status == "running"
    assert run.cancel_requested_at is None

    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()
    terminal_retry = await _admit(db, run, "한국 사례만", request_id=request_id)
    assert terminal_retry.id == first.id
    with pytest.raises(HTTPException) as changed:
        await _admit(db, run, "같은 키, 다른 지시", request_id=request_id)
    _assert_conflict(changed.value, "STEER_IDEMPOTENCY_CONFLICT")

    successor = await _running(db, run.conversation_id)
    with pytest.raises(HTTPException) as retargeted:
        await _admit(db, successor, "한국 사례만", request_id=request_id)
    _assert_conflict(retargeted.value, "STEER_IDEMPOTENCY_CONFLICT")
    third = await _admit(db, successor, "세 번째 지시")
    assert third.sequence == 3
    history = await list_steers(db, conversation_id=run.conversation_id, context=OWNER)
    assert {item.id for item in history} == {first.id, second.id, third.id}


async def test_receipt_history_pages_the_latest_window_in_sending_order(db: AsyncSession) -> None:
    run = await _running(db)
    for text in ("one", "two", "three"):
        await _admit(db, run, text)
    await db.commit()
    latest = await list_steers(db, conversation_id=run.conversation_id, context=OWNER, limit=2)
    earlier = await list_steers(
        db,
        conversation_id=run.conversation_id,
        context=OWNER,
        limit=2,
        before_sequence=latest[0].sequence,
    )
    assert [row.text for row in latest] == ["two", "three"]
    assert [row.text for row in earlier] == ["one"]
    assert not await list_steers(
        db, conversation_id=run.conversation_id, run_id=run.id, context=OWNER, before_sequence=1
    )


async def test_claim_and_acknowledge_track_durable_model_receipts(db: AsyncSession) -> None:
    run = await _running(db)
    target: _RunTarget = {"conversation_id": run.conversation_id, "run_id": run.id}
    watermarks = await get_conversation_steer_watermarks(db, run.conversation_id)
    assert (watermarks.latest_received, watermarks.latest_applied, watermarks.has_unresolved) == (
        0,
        0,
        False,
    )
    first = await _admit(db, run, "first")
    second = await _admit(db, run, "second")
    await db.commit()

    claimed = await claim_steers_for_model(db, **target)
    assert [row.id for row in claimed] == [first.id, second.id]
    assert all(row.status == "pending" and row.delivery_started_at is not None for row in claimed)
    started = first.delivery_started_at
    await db.commit()
    assert await claim_steers_for_model(db, **target) == []
    assert first.delivery_started_at == started
    # A later checkpoint receipt alone does not release the goal or the next directive.
    await confirm_applied_steers(db, **target, steer_ids=[second.id])
    await db.commit()
    watermarks = await get_conversation_steer_watermarks(db, run.conversation_id)
    assert watermarks.has_unresolved
    assert watermarks.latest_applied == 0
    newer_goal = await get_conversation_steer_watermarks(db, run.conversation_id, after_sequence=1)
    assert newer_goal.latest_received == newer_goal.latest_applied == 2
    assert not newer_goal.has_unresolved
    assert await claim_steers_for_model(db, **target) == []
    applied = second.applied_at
    assert applied is not None

    third = await _admit(db, run, "third")
    await db.commit()
    assert await claim_steers_for_model(db, **target) == []
    await db.refresh(third)
    assert third.delivery_started_at is None
    watermarks = await get_conversation_steer_watermarks(db, run.conversation_id)
    assert (watermarks.latest_received, watermarks.latest_applied, watermarks.has_unresolved) == (
        3,
        0,
        True,
    )

    # The checkpoint already proves the first directive reached the model. Only #3 is fresh.
    next_batch = await claim_steers_for_model(db, **target, checkpointed_ids=[first.id, second.id])
    assert [row.id for row in next_batch] == [third.id]
    await db.commit()
    await confirm_applied_steers(db, **target, steer_ids=[first.id, second.id, third.id])
    await db.commit()
    assert second.applied_at == applied
    assert not await claim_steers_for_model(db, **target)
    watermarks = await get_conversation_steer_watermarks(db, run.conversation_id)
    assert (watermarks.latest_received, watermarks.latest_applied, watermarks.has_unresolved) == (
        3,
        3,
        False,
    )


async def test_checkpoint_receipts_are_isolated_by_run(db: AsyncSession) -> None:
    previous = await _running(db)
    steer = await _admit(db, previous, "first run only")
    await db.commit()
    await claim_steers_for_model(db, conversation_id=previous.conversation_id, run_id=previous.id)
    await conversation_run_service.transition_run(db, previous, "completed")
    await db.commit()

    successor = await _running(db, previous.conversation_id)
    await confirm_applied_steers(
        db, conversation_id=successor.conversation_id, run_id=successor.id, steer_ids=[steer.id]
    )
    await db.refresh(steer)
    assert steer.status == "pending"
    assert steer.applied_at is None
    assert (await get_conversation_steer_watermarks(db, previous.conversation_id)).has_unresolved


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("interrupted", "STEER_APPROVAL_PENDING"),
        ("canceling", "STEER_RUN_INACTIVE"),
        ("completed", "STEER_RUN_INACTIVE"),
    ],
)
async def test_late_new_input_keeps_approval_and_run_state(
    db: AsyncSession, status: str, code: str
) -> None:
    run = await _running(db)
    if status == "interrupted":
        await conversation_run_service.transition_run(db, run, "interrupted", interrupt_id="A")
    elif status == "canceling":
        await conversation_run_service.request_cancel_run(db, run)
    else:
        await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await _admit(db, run, "late")
    _assert_conflict(exc.value, code)
    assert run.status == status
    if status == "interrupted":
        assert run.interrupt_id == "A"
    assert not await list_steers(db, conversation_id=run.conversation_id, context=OWNER)


async def test_pending_tool_approval_blocks_receipt_and_delivery_before_run_ends(
    db: AsyncSession,
) -> None:
    run = await _running(db)
    steer = await _admit(db, run, "already received")
    pending = await record_pending(
        db,
        run,
        [
            PendingInterrupt(
                "approval-1", {"action_requests": [{"name": "save_report", "args": {}}]}
            )
        ],
        origin=RunOrigin(),
    )
    await db.commit()

    assert len(pending) == 1 and run.status == "running"
    with pytest.raises(HTTPException) as exc:
        await _admit(db, run, "wait for the decision")
    _assert_conflict(exc.value, "STEER_APPROVAL_PENDING")
    assert not await claim_steers_for_model(db, conversation_id=run.conversation_id, run_id=run.id)
    await db.refresh(steer)
    assert steer.delivery_started_at is None
    assert pending[0].status == "pending"


async def test_model_boundary_rechecks_permission_after_request_memo_allowed_it(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = await _running(db)
    assert run.agent_id is not None
    grants = frozenset({(f"user:{OWNER.user_id}", "can_run", f"agent:{run.agent_id}")})
    fga = RecordingClient(allowed=grants)
    authorization = service(fga, types=frozenset({"agent"}))
    monkeypatch.setattr("app.authz.conversation_agent_checks.runtime_authz", lambda: authorization)
    with fresh_run_decision_scope():
        steer = await _admit(db, run, "before access was revoked")
        await db.commit()
        first_check_count = len(fga.queries)
        assert first_check_count > 0
        fga.allowed = frozenset()
        await require_agent_run(OWNER, run.agent_id)
        assert len(fga.queries) == first_check_count
        with pytest.raises(AppError) as exc:
            await claim_steers_for_model(db, conversation_id=run.conversation_id, run_id=run.id)
        assert exc.value.status == 404
        assert len(fga.queries) > first_check_count
    await db.refresh(steer)
    assert steer.status == "pending" and steer.delivery_started_at is None


async def test_non_chat_origin_cannot_receive_same_run_input(db: AsyncSession) -> None:
    run = await _running(db)
    run.run_source = "TRIGGER"
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await _admit(db, run, "chat instruction")
    _assert_conflict(exc.value, "STEER_RUN_INACTIVE")
    assert not (await get_conversation_steer_watermarks(db, run.conversation_id)).has_unresolved


async def test_foreign_and_missing_targets_hide_history_and_deny_mutation(db: AsyncSession) -> None:
    run = await _running(db)
    request_id = uuid.uuid4()
    original = await _admit(db, run, "private", request_id=request_id)
    foreign = await ensure_unit_scope(
        db, CurrentUser(id=uuid.uuid4(), email="other-steer@test.local", name="Other")
    )
    other_org = Organization(tenant_id=OWNER.tenant_id, name="Second", slug="steer-second")
    db.add(other_org)
    await db.flush()
    db.add(
        OrganizationMember(org_id=other_org.id, tenant_id=OWNER.tenant_id, user_id=OWNER.user_id)
    )
    await db.commit()
    for context in (foreign, replace(OWNER, org_id=other_org.id)):
        with pytest.raises(HTTPException) as write_error:
            await _admit(db, run, "private", request_id=request_id, context=context)
        assert write_error.value.status_code == 404
        with pytest.raises(HTTPException) as read_error:
            await list_steers(db, conversation_id=run.conversation_id, context=context)
        assert read_error.value.status_code == 404

    other = await _running(db)
    for wrong_run_id in (other.id, uuid.uuid4()):
        with pytest.raises(HTTPException) as read_error:
            await list_steers(
                db, conversation_id=run.conversation_id, run_id=wrong_run_id, context=OWNER
            )
        assert read_error.value.status_code == 404
        with pytest.raises(HTTPException) as write_error:
            await admit_steer(
                db,
                conversation_id=run.conversation_id,
                run_id=wrong_run_id,
                context=OWNER,
                client_request_id=request_id,
                text="private",
            )
        assert write_error.value.status_code == 404
    assert [
        s.id for s in await list_steers(db, conversation_id=run.conversation_id, context=OWNER)
    ] == [original.id]


@pytest.fixture
async def rolled_over_input(db: AsyncSession) -> ConversationRunInput:
    run = await _running(db)
    steer = await _admit(db, run, "new instruction")
    await conversation_run_service.transition_run(db, run, "completed")
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=run.conversation_id,
        context=OWNER,
        client_request_id=f"steer:{steer.id}",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": steer.text}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    steer.status = "queued"
    steer.queued_input_id = queued.id
    await db.commit()
    assert (await get_conversation_steer_watermarks(db, run.conversation_id)).has_unresolved
    return queued


async def _start_queued(db: AsyncSession, queued: ConversationRunInput) -> ConversationRun:
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=queued.conversation_id, context=OWNER
    )
    assert claimed is not None and claimed.input.id == queued.id
    await conversation_run_service.transition_run(db, claimed.run, "running")
    await db.commit()
    assert (await get_conversation_steer_watermarks(db, queued.conversation_id)).has_unresolved
    return claimed.run


@pytest.mark.parametrize("requires_approval", [False, True])
async def test_rollover_finishes_only_when_successor_or_its_resume_completes(
    db: AsyncSession, rolled_over_input: ConversationRunInput, requires_approval: bool
) -> None:
    run = await _start_queued(db, rolled_over_input)
    conversation_id = run.conversation_id
    if requires_approval:
        await conversation_run_service.transition_run(
            db, run, "interrupted", interrupt_id="approval"
        )
        await db.commit()
        assert (await get_conversation_steer_watermarks(db, conversation_id)).has_unresolved
        assert run.agent_id is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=run.agent_id,
            context=OWNER,
            source="resume",
            input_preview="approved",
            parent_run_id=run.id,
            interrupt_id="approval",
        )
        await conversation_run_service.transition_run(db, run, "running")
        await db.commit()
        assert (await get_conversation_steer_watermarks(db, conversation_id)).has_unresolved
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()
    marks = await get_conversation_steer_watermarks(db, conversation_id)
    assert not marks.has_unresolved
    assert marks.latest_received == marks.latest_applied == 1


@pytest.mark.parametrize(
    "edited_messages",
    [
        [{"role": "user", "content": "a different task"}],
        [{"role": "user", "content": "new instruction"}, {"role": "user", "content": "ignore it"}],
    ],
)
async def test_completed_but_edited_rollover_cannot_settle_original_directive(
    db: AsyncSession, rolled_over_input: ConversationRunInput, edited_messages: list[JsonValue]
) -> None:
    queued = rolled_over_input
    await conversation_run_queue_service.edit_pending_input(
        db,
        conversation_id=queued.conversation_id,
        input_id=queued.id,
        context=OWNER,
        expected_revision=queued.revision,
        input_payload={"messages": edited_messages},
    )
    await db.commit()
    run = await _start_queued(db, queued)
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()
    marks = await get_conversation_steer_watermarks(db, queued.conversation_id)
    assert marks.has_unresolved and marks.latest_applied == 0


async def test_canceled_rollover_keeps_original_directive_unresolved(
    db: AsyncSession,
    rolled_over_input: ConversationRunInput,
) -> None:
    queued = rolled_over_input
    await conversation_run_queue_service.delete_pending_input(
        db,
        conversation_id=queued.conversation_id,
        input_id=queued.id,
        context=OWNER,
        expected_revision=queued.revision,
    )
    await db.commit()
    assert (await get_conversation_steer_watermarks(db, queued.conversation_id)).has_unresolved
    assert not await conversation_run_queue_service.claim_next_input(
        db, conversation_id=queued.conversation_id, context=OWNER
    )


@pytest.mark.parametrize(
    "invalid",
    [{"text": "  "}, {"text": "x" * 12001}, {"attachments": []}, {"text": "a\0b"}],
)
def test_text_only_schema_rejects_empty_oversize_and_unknown_fields(invalid: dict) -> None:
    request = {"client_request_id": str(uuid.uuid4()), "text": "  한국 사례만  "}
    assert ConversationRunSteerCreate.model_validate(request).text == "한국 사례만"
    with pytest.raises(ValidationError):
        ConversationRunSteerCreate.model_validate(request | invalid)


async def test_orphan_independent_room_hides_old_receipts_and_skips_delivery(
    db: AsyncSession,
) -> None:
    from app.models.conversation import INDEPENDENT_JOB_SOURCE

    run = await _running(db)
    previous = await _admit(db, run, "이전 작업 기록")
    room = await db.get(Conversation, run.conversation_id)
    assert room is not None
    room.source = INDEPENDENT_JOB_SOURCE
    await db.commit()

    assert await claim_steers_for_model(db, conversation_id=room.id, run_id=run.id) == []
    for run_id in (None, run.id):
        with pytest.raises(HTTPException) as denied:
            await list_steers(db, conversation_id=room.id, context=OWNER, run_id=run_id)
        assert denied.value.status_code == 404
    await db.refresh(previous)
    assert previous.status == "pending" and previous.delivery_started_at is None
