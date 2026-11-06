"""Approval requests: recorded per approval interrupt, decided exactly once (plan 17.2)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import recording, service
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from tests.authz.g03_support import (
    approval_payload,
    commands_url,
    respond_command,
    resume_runs,
    seed_conversation,
    seed_interrupted,
)

SECRET = "sk-live-approval-secret-123"
ASK = {
    "action_requests": [{"name": "ask_user", "args": {"question": "?"}}],
    "review_configs": [{"action_name": "ask_user", "allowed_decisions": ["respond"]}],
}


def _payload(**args: Any) -> dict[str, Any]:
    payload = approval_payload()
    payload["action_requests"][1]["args"] = {"to": "a@example.com", **args}
    return payload


async def _interrupted_run(db: AsyncSession, conversation: Conversation) -> ConversationRun:
    run_id = await seed_interrupted(db, conversation, approval_payload())
    run = await db.get(ConversationRun, run_id)
    assert run is not None
    run.tenant_id, run.org_id = conversation.tenant_id, conversation.org_id
    run.run_source = "TRIGGER"
    await db.commit()
    return run


async def _record(
    db: AsyncSession, run: ConversationRun, *interrupts: service.PendingInterrupt
) -> list[ApprovalRequest]:
    rows = await service.record_pending(
        db, run, interrupts, origin=service.RunOrigin("Mail agent"), secret_values=[SECRET]
    )
    await db.commit()
    return rows


@pytest.mark.asyncio
async def test_only_approval_interrupts_get_a_row_with_masked_arguments(db: AsyncSession) -> None:
    conversation = await seed_conversation(db)
    run = await _interrupted_run(db, conversation)
    payload = _payload(token=SECRET)

    rows = await _record(
        db,
        run,
        service.PendingInterrupt("intr-1", payload),
        service.PendingInterrupt("ask-1", ASK),
    )

    [row] = rows
    assert (row.interrupt_id, row.status, row.source, row.attempt) == (
        "intr-1",
        "pending",
        "trigger",
        1,
    )
    assert (row.subject_user_id, row.decider_user_id) == (run.user_id, run.user_id)
    assert row.policy_version == run.runtime_policy_version and row.agent_name == "Mail agent"
    assert [a["name"] for a in row.actions] == ["execute_in_skill", "send_email"]
    assert row.actions[1]["allowed_decisions"] == ["approve", "edit", "reject"]
    assert SECRET not in str(row.actions)
    assert row.actions[1]["args"]["to"] == "a@example.com"
    assert row.args_digest == service.args_digest(payload)  # of the real arguments
    expires = service._aware(row.expires_at) - service.now()
    assert timedelta(hours=23) < expires <= timedelta(hours=24)
    # Recording twice (a retried hook) adds nothing.
    assert await _record(db, run, service.PendingInterrupt("intr-1", payload)) == []


@pytest.mark.asyncio
async def test_the_same_call_asked_again_is_the_next_attempt(db: AsyncSession) -> None:
    conversation = await seed_conversation(db)
    first = await _interrupted_run(db, conversation)
    await _record(db, first, service.PendingInterrupt("intr-1", _payload()))
    second = await _interrupted_run(db, conversation)

    [again] = await _record(db, second, service.PendingInterrupt("intr-2", _payload()))

    assert again.attempt == 2


async def _pending(db: AsyncSession) -> tuple[ConversationRun, ApprovalRequest]:
    conversation = await seed_conversation(db)
    run = await _interrupted_run(db, conversation)
    [row] = await _record(db, run, service.PendingInterrupt("intr-1", _payload()))
    return run, row


def _claim(run: ConversationRun, *decisions: str, channel: str = "inbox") -> service.Claim:
    return service.Claim(
        run_id=run.id,
        decisions={"intr-1": list(decisions)},
        channel=channel,
        actor_id=run.user_id,
        resumed_run_id=None,
    )


@pytest.mark.asyncio
async def test_a_request_is_decided_exactly_once(db: AsyncSession) -> None:
    run, row = await _pending(db)

    [decided] = await service.claim(db, _claim(run, "approve", "reject"))
    await db.commit()

    assert (decided.status, decided.channel, decided.decision_no) == ("approved", "inbox", 1)
    assert decided.decisions == ["approve", "reject"] and decided.decided_by == run.user_id
    with pytest.raises(service.ApprovalStale) as stale:
        await service.claim(db, _claim(run, "reject", "reject", channel="chat"))
    assert stale.value.status == "approved"
    await db.refresh(row)
    assert (row.status, row.decision_no) == ("approved", 1)


@pytest.mark.parametrize(
    ("decisions", "status"),
    [(("reject", "reject"), "rejected"), (("approve", "edit"), "edited")],
)
@pytest.mark.asyncio
async def test_the_decision_names_the_outcome(
    db: AsyncSession, decisions: tuple[str, ...], status: str
) -> None:
    run, _row = await _pending(db)
    [decided] = await service.claim(db, _claim(run, *decisions))
    assert decided.status == status


@pytest.mark.asyncio
async def test_an_expired_request_can_no_longer_be_approved(db: AsyncSession) -> None:
    run, row = await _pending(db)
    row.expires_at = service.now() - timedelta(seconds=1)
    await db.commit()

    with pytest.raises(service.ApprovalStale) as stale:
        await service.claim(db, _claim(run, "approve", "approve"))
    assert stale.value.status == "expired"
    assert [r.id for r in await service.due_for_expiry(db)] == [row.id]

    [expired] = await service.claim(db, _claim(run, "reject", "reject", channel="expiry"))
    assert (expired.status, expired.channel) == ("expired", "expiry")


def _no_worker(monkeypatch: pytest.MonkeyPatch, target: str) -> list[dict[str, Any]]:
    started: list[dict[str, Any]] = []

    async def start(**kwargs: Any) -> None:
        started.append(kwargs)

    monkeypatch.setattr(target, start)
    return started


_PROTOCOL = "app.routers.conversation_agent_protocol.start_conversation_run"
_REST = "app.routers.chat_resume_entries.start_conversation_run"
_APPROVE = {"decisions": [{"type": "approve"}, {"type": "approve"}]}


@pytest.mark.asyncio
async def test_the_chat_card_claims_the_request_it_answers(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, row = await _pending(db)
    started = _no_worker(monkeypatch, _PROTOCOL)
    conversation = await db.get(Conversation, run.conversation_id)
    assert conversation is not None

    result = await client.post(commands_url(conversation), json=respond_command(_APPROVE))

    assert result.status_code == 200, result.text
    [resumed] = await resume_runs(db, conversation)
    await db.refresh(row)
    assert (row.status, row.channel, row.resumed_run_id) == ("approved", "chat", resumed.id)
    assert len(started) == 1


@pytest.mark.asyncio
async def test_a_card_answer_after_the_inbox_decided_is_stale_and_starts_nothing(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _row = await _pending(db)
    await service.claim(db, _claim(run, "reject", "reject"))
    await db.commit()
    started = _no_worker(monkeypatch, _PROTOCOL)
    conversation = await db.get(Conversation, run.conversation_id)
    assert conversation is not None

    result = await client.post(commands_url(conversation), json=respond_command(_APPROVE))

    assert result.status_code == 409
    assert result.json()["error"]["code"] == "STALE_INTERRUPT"
    assert await resume_runs(db, conversation) == [] and started == []


@pytest.mark.asyncio
async def test_the_rest_resume_after_expiry_is_refused(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, row = await _pending(db)
    row.expires_at = service.now() - timedelta(minutes=1)
    await db.commit()
    started = _no_worker(monkeypatch, _REST)
    conversation = await db.get(Conversation, run.conversation_id)
    assert conversation is not None

    result = await client.post(
        f"/api/conversations/{run.conversation_id}/messages/resume", json=_APPROVE
    )

    assert result.status_code == 409
    assert await resume_runs(db, conversation) == [] and started == []


@pytest.mark.asyncio
async def test_the_worker_hook_records_from_the_checkpoint_and_notifies_unattended(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.authz.org_context import OrgContext
    from app.models.notification import Notification
    from tests.conftest import TEST_USER_ID

    conversation = await seed_conversation(db)
    run = await _interrupted_run(db, conversation)

    async def checkpoint(_id: uuid.UUID) -> list[service.PendingInterrupt]:
        return [service.PendingInterrupt("intr-1", _payload())]

    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    context = OrgContext(
        tenant_id=conversation.tenant_id, org_id=conversation.org_id, user_id=TEST_USER_ID
    )

    [row] = await recording.record_interrupted_run(run.id, context)

    notes = list(
        await db.scalars(select(Notification).where(Notification.kind == "approval.requested"))
    )
    assert [n.target_id for n in notes] == [row.id]
    assert notes[0].user_id == row.decider_user_id and notes[0].link == "/approvals"
