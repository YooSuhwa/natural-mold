"""Verify that a normal AG-UI command owns exactly one transactional chat input."""

from __future__ import annotations

import hashlib
from uuid import UUID

import pytest
from ag_ui.core import RunAgentInput
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import ConflictError
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.services import ag_ui_commands, conversation_run_queue_service, conversation_run_service
from app.services.ag_ui_start import accept_start, wake_queued_start
from app.services.ag_ui_start_input import start_message_id
from app.services.conversation_run_worker import get_run_task_registry
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent

pytestmark = pytest.mark.asyncio


def _wire(
    conversation_id: UUID, *, text: str = "hello", strategy: str = "enqueue"
) -> RunAgentInput:
    return RunAgentInput.model_validate(
        {
            "threadId": str(conversation_id),
            "runId": "client/tab/92",
            "messages": [
                {"id": "untrusted-history", "role": "assistant", "content": "old browser copy"},
                {"id": "local-input", "role": "user", "content": text},
            ],
            "forwardedProps": {"moldy": {"strategy": strategy}},
        }
    )


async def _submit(
    db: AsyncSession,
    wire: RunAgentInput,
    *,
    request: Request | None = None,
    context: OrgContext | None = None,
) -> ag_ui_commands.AgUiRunBinding:
    context = context or unit_context(TEST_USER_ID)
    conversation_id = UUID(wire.thread_id)
    digest = hashlib.sha256(wire.model_dump_json(by_alias=True).encode()).hexdigest()
    held = await ag_ui_commands.find_or_reserve(db, context, conversation_id, wire.run_id, digest)
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    return await accept_start(
        db=db,
        context=context,
        user=CurrentUser(id=context.user_id, name="Test", email="test@test.com"),
        conversation=conversation,
        request=request or Request({"type": "http", "headers": []}),
        run_input=wire,
        admission=held,
    )


async def _sent_actions(db: AsyncSession, conversation_id: UUID) -> list[str]:
    return list(
        await db.scalars(
            select(AuditEvent.action).where(
                AuditEvent.target_id == str(conversation_id),
                AuditEvent.action.in_(
                    ("conversation.message_send", "conversation.run_steer_request")
                ),
            )
        )
    )


async def test_acceptance_rolls_back_queue_receipt_draft_and_audit_together(
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.source = "draft"
    await db.commit()
    wire = _wire(conversation_id)
    binding = await _submit(db, wire)
    assert binding.input_id is not None and binding.run_id is None
    input_row = await db.get(ConversationRunInput, binding.input_id)
    assert input_row is not None
    assert input_row.input_payload == {
        "messages": [
            {
                "role": "user",
                "content": "hello",
                "id": start_message_id(conversation_id, wire.run_id, "local-input"),
            }
        ]
    }
    assert conversation.source == "ui"
    assert await _sent_actions(db, conversation_id) == ["conversation.message_send"]
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 0
    await db.rollback()

    assert await db.get(ConversationRunInput, binding.input_id) is None
    assert await db.get(AgUiCommandReceipt, (conversation_id, wire.run_id)) is None
    await db.refresh(conversation)
    assert conversation.source == "draft" and conversation.runtime_policy_snapshot is None
    assert await _sent_actions(db, conversation_id) == []
    accepted = await _submit(db, wire)
    await db.commit()
    assert accepted.input_id != binding.input_id
    assert await _submit(db, wire) == accepted
    with pytest.raises(ConflictError) as conflicting:
        await _submit(db, _wire(conversation_id, text="changed"))
    assert conflicting.value.code == "AG_UI_RUN_ID_CONFLICT"
    assert await db.scalar(select(func.count()).select_from(ConversationRunInput)) == 1
    assert await _sent_actions(db, conversation_id) == ["conversation.message_send"]


async def test_matching_legacy_input_cannot_be_adopted_as_public_run(db: AsyncSession) -> None:
    conversation_id = await seed_conversation_with_agent()
    wire = _wire(conversation_id)
    old = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=unit_context(TEST_USER_ID),
        client_request_id="agui:" + hashlib.sha256(wire.run_id.encode()).hexdigest(),
        source="chat",
        input_payload={
            "messages": [
                {
                    "role": "user",
                    "content": "hello",
                    "id": start_message_id(conversation_id, wire.run_id, "local-input"),
                }
            ]
        },
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    with pytest.raises(HTTPException) as conflict:
        await _submit(db, wire)
    assert conflict.value.status_code == 409
    assert await db.get(AgUiCommandReceipt, (conversation_id, wire.run_id)) is None
    assert list(await db.scalars(select(ConversationRunInput.id))) == [old.id]
    assert await _sent_actions(db, conversation_id) == []


async def _active(db: AsyncSession, conversation_id: UUID) -> ConversationRun:
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conversation),
        context=unit_context(TEST_USER_ID),
        source="chat",
        input_preview="initial task",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="worker-A")
    return run


async def test_interrupt_is_durable_before_local_wake(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await seed_conversation_with_agent()
    run = await _active(db, conversation_id)
    await db.commit()
    seen: list[tuple[UUID, str]] = []

    def remember(run_id: UUID, *, reason: str) -> bool:
        seen.append((run_id, reason))
        return True

    monkeypatch.setattr(get_run_task_registry(), "request_cancel", remember)
    request = Request({"type": "http", "headers": []})
    wire = _wire(conversation_id, strategy="interrupt")
    binding = await _submit(db, wire, request=request)
    await db.commit()
    assert binding.input_id is not None and binding.run_id is None
    assert seen == []
    await db.refresh(run)
    assert (run.status, run.cancel_reason) == ("canceling", "steer")
    assert run.cancel_requested_at is not None
    queued = await db.get(ConversationRunInput, binding.input_id)
    assert queued is not None and queued.priority == 100 and queued.run_id is None
    await wake_queued_start(request, conversation_id)
    assert seen == [(run.id, "steer")]
    assert await _submit(db, wire) == binding
    assert sorted(await _sent_actions(db, conversation_id)) == [
        "conversation.message_send",
        "conversation.run_steer_request",
    ]
    assert list(await db.scalars(select(ConversationRun.id))) == [run.id]


async def test_pending_approval_refuses_new_command_before_queue_or_audit(db: AsyncSession) -> None:
    conversation_id = await seed_conversation_with_agent()
    run = await _active(db, conversation_id)
    await conversation_run_service.transition_run(db, run, "interrupted", interrupt_id="pending-id")
    await db.commit()
    wire = _wire(conversation_id, strategy="interrupt")
    with pytest.raises(HTTPException) as blocked:
        await _submit(db, wire)
    assert blocked.value.status_code == 409
    assert (run.status, run.interrupt_id, run.cancel_requested_at) == (
        "interrupted",
        "pending-id",
        None,
    )
    assert await db.get(AgUiCommandReceipt, (conversation_id, wire.run_id)) is None
    assert await db.scalar(select(func.count()).select_from(ConversationRunInput)) == 0
    assert await _sent_actions(db, conversation_id) == []


async def test_independent_child_refuses_ordinary_send(
    db: AsyncSession,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
) -> None:
    context, _parent, child_run, job = delegation
    conversation_id = job.child_conversation_id
    wire = _wire(conversation_id)
    with pytest.raises(HTTPException) as blocked:
        await _submit(db, wire, context=context)
    assert blocked.value.status_code == 409
    assert await db.get(AgUiCommandReceipt, (conversation_id, wire.run_id)) is None
    assert child_run.cancel_requested_at is None
    assert not await db.scalar(
        select(ConversationRunInput.id).where(
            ConversationRunInput.conversation_id == conversation_id
        )
    )
    assert await _sent_actions(db, conversation_id) == []
