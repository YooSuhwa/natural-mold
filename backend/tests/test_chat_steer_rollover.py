from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from langgraph.checkpoint.base import ChannelVersions, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.model_input_receipt import text_digest
from app.authz.conversation_scope import require_conversation_agent_id
from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_steer import ConversationRunSteer
from app.models.organization import OrganizationMember
from app.schemas.conversation_graph_input import STEER_RECEIPTS_CHANNEL
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.conversation_run_creation import RunStatus
from app.services.conversation_run_steer_rollover import (
    reconcile_terminal_steers,
    recover_terminal_steers,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent

OWNER = unit_context(TEST_USER_ID)


async def _run_with_receipts(
    db: AsyncSession, *texts: str
) -> tuple[ConversationRun, list[ConversationRunSteer]]:
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        context=OWNER,
        agent_id=require_conversation_agent_id(conversation),
        source="chat",
        input_preview="Research",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="worker")
    rows = [
        ConversationRunSteer(
            user_id=OWNER.user_id,
            org_id=OWNER.org_id,
            tenant_id=OWNER.tenant_id,
            conversation_id=conversation_id,
            run_id=run.id,
            sequence=sequence,
            client_request_id=uuid4(),
            text=text,
        )
        for sequence, text in enumerate(texts, start=1)
    ]
    db.add_all(rows)
    await db.commit()
    return run, rows


async def _settle(db: AsyncSession, run: ConversationRun) -> None:
    await reconcile_terminal_steers(
        db, conversation_id=run.conversation_id, run_id=run.id, context=OWNER
    )
    await db.commit()


async def _enqueue(
    db: AsyncSession, run: ConversationRun, content: str, request_id: str
) -> ConversationRunInput:
    return await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=run.conversation_id,
        context=OWNER,
        source="chat",
        client_request_id=request_id,
        checkpoint_id=None,
        attachment_ids=[],
        input_payload={"messages": [{"role": "user", "content": content}]},
        priority=0,
    )


@pytest.mark.parametrize(
    ("status", "error_code", "paused"),
    [
        ("completed", None, False),
        ("failed", "stream_error", False),
        ("failed", "prepared_run_lease_missing", True),
        ("interrupted", None, False),
        ("canceled", None, True),
        ("stale", "stale_heartbeat", True),
    ],
)
async def test_unattempted_instructions_survive_terminal_once_in_fifo(
    db: AsyncSession, status: RunStatus, error_code: str | None, paused: bool
) -> None:
    run, rows = await _run_with_receipts(db, "한국 사례만", "날짜를 표에 추가해줘")
    first = await _enqueue(db, run, "다음 요청", "normal-queue")
    await _settle(db, run)
    assert [row.status for row in rows] == ["pending", "pending"]
    if status == "canceled":
        await conversation_run_service.request_cancel_run(db, run, reason="stop")
        await conversation_run_queue_service.pause_queue(
            db, conversation_id=run.conversation_id, context=OWNER
        )
    await conversation_run_service.transition_run(
        db,
        run,
        status,
        error_code=error_code,
        cancellation_ack_worker_id="worker",
        interrupt_id="approval-1" if status == "interrupted" else None,
    )
    await db.commit()
    await _settle(db, run)
    await _settle(db, run)
    inputs = (
        await db.scalars(select(ConversationRunInput).order_by(ConversationRunInput.position))
    ).all()
    assert [item.id for item in inputs] == [first.id, *(row.queued_input_id for row in rows)]
    assert [item.input_payload["messages"] for item in inputs[1:]] == [
        [{"role": "user", "content": row.text}] for row in rows
    ]
    assert [row.status for row in rows] == ["queued", "queued"]
    assert all(item.run_id is None and item.status == "pending" for item in inputs)
    conversation = await db.get(Conversation, run.conversation_id)
    assert conversation is not None and conversation.queue_paused is paused
    if status == "interrupted":
        assert (
            await conversation_run_queue_service.claim_next_input(
                db, conversation_id=run.conversation_id, context=OWNER
            )
            is None
        )
        assert run.interrupt_id == "approval-1"


async def test_restart_after_checkpoint_before_ack_verifies_receipts_and_drains_when_disabled(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, (proven, uncertain, unsent) = await _run_with_receipts(db, "A", "B", "C")
    proven.delivery_started_at = uncertain.delivery_started_at = utc_now_naive()
    saver = InMemorySaver()
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {
        STEER_RECEIPTS_CHANNEL: {
            "run_id": str(run.id),
            "ids": [str(proven.id)],
            "staged": {str(uncertain.id): text_digest(uncertain.text)},
        }
    }
    versions: ChannelVersions = {STEER_RECEIPTS_CHANNEL: saver.get_next_version(None, None)}
    checkpoint["channel_versions"] = versions
    await saver.aput(
        {
            "configurable": {
                "thread_id": str(run.conversation_id),
                "checkpoint_ns": "",
                "moldy_tenant_id": str(OWNER.tenant_id),
                "moldy_run_id": str(run.id),
            }
        },
        checkpoint,
        {"step": 1},
        versions,
    )
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()  # worker is gone before its DB receipt/queue write
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    monkeypatch.setattr(settings, "chat_steer_enabled", False)
    for _ in range(2):
        await recover_terminal_steers(TestSession, lambda _scope: TestSession())
    for row in (proven, uncertain, unsent):
        await db.refresh(row)
    assert (proven.status, uncertain.status, unsent.status) == ("applied", "needs_review", "queued")
    assert proven.applied_at is not None
    assert proven.queued_input_id is None and uncertain.queued_input_id is None
    assert list(await db.scalars(select(ConversationRunInput.id))) == [unsent.queued_input_id]
    conversation = await db.get(Conversation, run.conversation_id)
    assert conversation is not None
    await db.refresh(conversation)
    assert conversation.queue_paused


async def test_revoked_owner_and_wrong_tenant_never_create_queue_work(db: AsyncSession) -> None:
    run, [row] = await _run_with_receipts(db, "private instruction")
    row.delivery_started_at = utc_now_naive()
    await conversation_run_service.transition_run(db, run, "failed")
    await db.commit()
    await reconcile_terminal_steers(
        db,
        conversation_id=run.conversation_id,
        run_id=run.id,
        context=replace(OWNER, tenant_id=uuid4()),
    )
    assert row.status == "pending"
    await db.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.org_id == OWNER.org_id, OrganizationMember.user_id == OWNER.user_id
        )
        .values(status="removed")
    )
    await db.commit()
    await _settle(db, run)
    await db.refresh(row)
    assert row.status == "needs_review"
    assert await db.scalar(select(ConversationRunInput.id)) is None


async def test_other_queue_entry_cannot_be_adopted_as_a_rollover(db: AsyncSession) -> None:
    run, [row] = await _run_with_receipts(db, "confidential")
    imposter = await _enqueue(db, run, "different text", f"steer:{row.id}")
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()
    await _settle(db, run)
    assert row.status == "needs_review" and row.queued_input_id is None
    assert list(await db.scalars(select(ConversationRunInput.id))) == [imposter.id]


async def test_recovery_ignores_instruction_bound_to_another_conversation(db: AsyncSession) -> None:
    run, [valid] = await _run_with_receipts(db, "원래 지시")
    other_id = await seed_conversation_with_agent()
    crossed = ConversationRunSteer(
        conversation_id=other_id,
        run_id=run.id,
        user_id=OWNER.user_id,
        org_id=OWNER.org_id,
        tenant_id=OWNER.tenant_id,
        sequence=1,
        client_request_id=uuid4(),
        text="다른 대화의 지시",
    )
    db.add(crossed)
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()

    await recover_terminal_steers(TestSession, lambda _scope: TestSession())
    await db.refresh(valid)
    await db.refresh(crossed)
    assert valid.status == "queued"
    assert crossed.status == "pending" and crossed.queued_input_id is None
    assert list(await db.scalars(select(ConversationRunInput.conversation_id))) == [
        run.conversation_id
    ]
