"""Distinguish a question, connection prompt and tool consent on the ordinary chat path."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.protocol_persistence import persistable_protocol_event
from app.approvals import service
from app.config import settings
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_goal import GoalAttempt, GoalTransitionIntent
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent, MessageEventChunk
from app.services import conversation_goal_worker as worker
from app.services import conversation_run_service as runs
from app.services.chat_notification_outbox import drain_chat_notification_outbox
from tests.authz.g03_support import (
    approval_payload,
    auth_payload,
    commands_url,
    respond_command,
    resume_runs,
)
from tests.test_conversation_goal_worker import _accept, _seed

IID = "pending-input"
PRIVATE_PROMPT = "fixture-question-text-private-to-this-conversation"
WRAPPED_QUESTION = {
    "action_requests": [{"name": "ask_user", "args": {"question": PRIVATE_PROMPT}}],
    "review_configs": [{"action_name": "ask_user", "allowed_decisions": ["respond"]}],
}


async def _persist_question(
    db: AsyncSession,
    conversation: Conversation,
    run: ConversationRun,
    payload: dict[str, Any],
    *,
    chunked: bool = False,
    wrong_id: str | None = None,
) -> None:
    ids = {"run": str(run.id), "thread": str(conversation.id), "interrupt": IID}
    if wrong_id:
        ids[wrong_id] = str(uuid4())
    event = persistable_protocol_event(
        stored_protocol_event(
            run_id=ids["run"],
            thread_id=ids["thread"],
            seq=1,
            method="input.requested",
            namespace=[],
            data={"interrupt_id": ids["interrupt"], "payload": payload},
        )
    )
    row = MessageEvent(
        conversation_id=conversation.id,
        assistant_msg_id=str(run.id),
        status="completed",
        events=[] if chunked else [event],
    )
    db.add(row)
    await db.flush()
    if chunked:
        db.add(
            MessageEventChunk(
                message_event_id=row.id,
                conversation_id=conversation.id,
                assistant_msg_id=str(run.id),
                seq_start=1,
                seq_end=1,
                first_event_id=event["id"],
                last_event_id=event["id"],
                event_ids=[event["id"]],
                events=[event],
            )
        )
    await db.commit()


async def _interrupt_and_reconcile(
    db: AsyncSession, conversation: Conversation, run: ConversationRun
) -> None:
    await runs.transition_run(db, run, "running", worker_instance_id="goal-worker")
    await runs.transition_run(db, run, "interrupted", interrupt_id=IID)
    await db.commit()
    for _ in range(2):
        await worker.reconcile_goal_run(db, run.id, conversation.id)
        await db.commit()
        await drain_chat_notification_outbox(db, tenant_id=conversation.tenant_id)
        await db.commit()


@pytest.mark.parametrize("prompt", ["ask_user", "wrapped_question", "connection", "approval"])
async def test_only_tool_approval_uses_approval_inbox_and_every_answer_continues_the_same_goal(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    conversation, goal = await _seed(db, monkeypatch)
    run = await _accept(db, conversation)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    if prompt == "ask_user":
        payload = {"type": "ask_user", "question": PRIVATE_PROMPT, "options": []}
        answer = {"text": "우리 조직만"}
    elif prompt == "wrapped_question":
        payload = WRAPPED_QUESTION
        answer = {"decisions": [{"type": "respond", "message": "우리 조직만"}]}
    elif prompt == "connection":
        payload = auth_payload()
        answer = {"decisions": [{"type": "respond", "message": "retry", "attempt": 1}]}
    else:
        payload = approval_payload()
        answer = {"decisions": [{"type": "reject"}, {"type": "reject"}]}
        await service.record_pending(
            db, run, [service.PendingInterrupt(IID, payload)], origin=service.RunOrigin("Test")
        )
    await _persist_question(db, conversation, run, payload, chunked=prompt == "connection")
    await _interrupt_and_reconcile(db, conversation, run)

    goal_url = f"/api/conversations/{conversation.id}/goal"
    observed = (await client.get(goal_url)).json()["goal"]
    assert observed["status"] == "blocked"
    assert observed["blocked_reason"] == (
        "approval_pending" if prompt == "approval" else "input_required"
    )
    manual_resume = await client.post(
        f"{goal_url}/actions", json={"expected_revision": observed["revision"], "action": "resume"}
    )
    assert manual_resume.status_code == 409 and observed["blocked_reason"] in manual_resume.text
    [transition] = (await db.scalars(select(GoalTransitionIntent))).all()
    assert transition.goal_id == goal.id and transition.run_id == run.id
    bell = await client.get("/api/notifications")
    assert bell.status_code == 200
    [notice] = bell.json()["items"]
    assert PRIVATE_PROMPT not in bell.text and "cred-1" not in bell.text
    if prompt == "approval":
        [request] = (await db.scalars(select(ApprovalRequest))).all()
        assert (notice["kind"], notice["target"]["id"]) == ("approval.requested", str(request.id))
    else:
        assert notice["kind"] == "chat_goal.blocked"
        opened = await client.get(f"/api/notifications/{notice['id']}/destination")
        assert opened.status_code == 200 and opened.json()["goal_id"] == str(goal.id)
        assert await db.scalar(select(ApprovalRequest.id)) is None

    # The HTTP command still builds, authorizes, creates and claims the resumed run.
    # Keep execution out of this isolated database after its durable acceptance.
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run", AsyncMock()
    )
    response = await client.post(commands_url(conversation), json=respond_command(answer, iid=IID))
    assert response.status_code == 200, response.text
    [resumed] = await resume_runs(db, conversation)
    attempt = (await db.scalars(select(GoalAttempt).where(GoalAttempt.run_id == resumed.id))).one()
    assert resumed.parent_run_id == run.id
    assert (attempt.goal_id, attempt.goal_version) == (goal.id, goal.version)
    active = (await client.get(goal_url)).json()["goal"]
    assert (active["status"], active["blocked_reason"]) == ("active", None)


@pytest.mark.parametrize("evidence", ["missing", "unknown", "run", "thread", "interrupt"])
async def test_unconfirmed_or_foreign_interrupt_never_creates_input_or_approval_notice(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, evidence: str
) -> None:
    conversation, _goal = await _seed(db, monkeypatch)
    run = await _accept(db, conversation)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    if evidence != "missing":
        unknown = {"type": "unrecognized", "hint": PRIVATE_PROMPT}
        await _persist_question(
            db,
            conversation,
            run,
            unknown if evidence == "unknown" else WRAPPED_QUESTION,
            wrong_id=None if evidence == "unknown" else evidence,
        )
    # There is no live checkpoint in this isolated lane. The durable transition
    # must not use an unrelated question as proof of what this thread needs.
    await _interrupt_and_reconcile(db, conversation, run)
    response = await client.get("/api/notifications")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert await db.scalar(select(ApprovalRequest.id)) is None
    observed = (await client.get(f"/api/conversations/{conversation.id}/goal")).json()["goal"]
    assert observed["status"] == "blocked" and observed["blocked_reason"] != "input_required"
    assert PRIVATE_PROMPT not in response.text


async def test_checkpoint_question_recovers_a_missing_trace_in_its_own_thread(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, goal = await _seed(db, monkeypatch)
    run = await _accept(db, conversation)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    saver = InMemorySaver()
    for thread_id, value in (
        (str(uuid4()), approval_payload()),
        (str(conversation.id), auth_payload()),
    ):
        cfg = await saver.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            empty_checkpoint(),
            {},
            {},
        )
        await saver.aput_writes(cfg, [("__interrupt__", [Interrupt(id=IID, value=value)])], "agent")
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saver)
    await _interrupt_and_reconcile(db, conversation, run)
    await db.refresh(goal)
    assert goal.blocked_reason == "input_required"
    assert await db.scalar(select(ApprovalRequest.id)) is None
    [notice] = (await client.get("/api/notifications")).json()["items"]
    assert notice["kind"] == "chat_goal.blocked"
