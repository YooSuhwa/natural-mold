"""The approval inbox: list, decide once, recheck before resuming, expire (plan 17.2)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import inbox, service
from app.dependencies import CurrentUser
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.notification import Notification
from app.models.organization import OrganizationMember
from app.models.user import User
from tests.authz.g03_support import (
    approval_payload,
    resume_runs,
    seed_conversation,
    seed_interrupted,
)
from tests.authz.unit_scope_fixture import ensure_unit_scope
from tests.conftest import TEST_USER_ID

_START = "app.routers.conversation_agent_protocol.start_conversation_run"
APPROVE = {"decisions": [{"type": "approve"}, {"type": "approve"}]}


async def _request(
    db: AsyncSession, *, payload: dict[str, Any] | None = None, decider: uuid.UUID | None = None
) -> tuple[Conversation, ApprovalRequest]:
    conversation = await seed_conversation(db)
    user = await db.get(User, TEST_USER_ID)
    assert user is not None
    await ensure_unit_scope(db, CurrentUser(id=user.id, email=user.email, name=user.name))
    shown = payload or approval_payload()
    run = await db.get(ConversationRun, await seed_interrupted(db, conversation, shown))
    assert run is not None
    run.tenant_id, run.org_id, run.run_source = (
        conversation.tenant_id,
        conversation.org_id,
        "TRIGGER",
    )
    await db.commit()
    [row] = await service.record_pending(
        db,
        run,
        [service.PendingInterrupt("intr-1", approval_payload())],
        origin=service.RunOrigin("Mail agent"),
    )
    if decider is not None:
        row.decider_user_id = decider
    await db.commit()
    return conversation, row


def _started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    started: list[dict[str, Any]] = []

    async def start(**kwargs: Any) -> None:
        started.append(kwargs)

    monkeypatch.setattr(_START, start)
    return started


async def _other_user(db: AsyncSession) -> uuid.UUID:
    other = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@x.dev", name="Other")
    db.add(other)
    await db.commit()
    return other.id


def _url(row: ApprovalRequest) -> str:
    return f"/api/approvals/{row.id}/decision"


@pytest.mark.asyncio
async def test_the_inbox_lists_only_my_requests_with_what_is_needed_to_decide(
    client: AsyncClient, db: AsyncSession
) -> None:
    _conversation, mine = await _request(db)
    await _request(db, decider=await _other_user(db))

    listed = (await client.get("/api/approvals")).json()["items"]
    count = (await client.get("/api/approvals/pending-count")).json()

    assert [item["id"] for item in listed] == [str(mine.id)] and count == {"count": 1}
    [item] = listed
    assert item["agent_name"] == "Mail agent" and item["source"] == "trigger"
    assert [a["name"] for a in item["actions"]] == ["execute_in_skill", "send_email"]
    assert item["actions"][0]["allowed_decisions"] == ["approve", "reject"]
    assert "messages" not in item  # the decision view, not the conversation
    assert (await client.get("/api/approvals?status=done")).json()["items"] == []


@pytest.mark.asyncio
async def test_a_decision_resumes_the_run_once_through_the_chat_path(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, row = await _request(db)
    started = _started(monkeypatch)
    body = {
        "decisions": [
            {"type": "reject", "message": "not now"},
            {"type": "edit", "edited_action": {"args": {"to": "b@example.com"}}},
        ]
    }

    result = await client.post(_url(row), json=body)

    assert result.status_code == 200, result.text
    assert (result.json()["status"], result.json()["channel"]) == ("edited", "inbox")
    [resumed] = await resume_runs(db, conversation)
    await db.refresh(row)
    assert row.resumed_run_id == resumed.id and row.decided_by == TEST_USER_ID
    [start] = started
    assert start["moldy_source"] == "resume"
    sent = start["input_payload"]["decisions"]
    assert sent[0] == {"type": "reject", "message": "not now"}
    assert sent[1]["edited_action"]["args"] == {"to": "b@example.com"}
    assert sent[1]["edited_action"]["name"] == "send_email"  # the slot's tool, not a choice

    again = await client.post(_url(row), json=APPROVE)
    assert again.status_code == 409 and again.json()["error"]["code"] == "approval_stale"
    assert len(started) == 1


@pytest.mark.asyncio
async def test_someone_else_cannot_decide_my_request(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conversation, row = await _request(db, decider=await _other_user(db))
    started = _started(monkeypatch)

    result = await client.post(_url(row), json=APPROVE)

    assert result.status_code == 404 and started == []
    await db.refresh(row)
    assert row.status == "pending"


@pytest.mark.asyncio
async def test_a_decision_outside_the_allow_list_is_refused(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conversation, row = await _request(db)
    started = _started(monkeypatch)
    body = {
        "decisions": [
            {"type": "edit", "edited_action": {"args": {}}},  # execute_in_skill: no edit
            {"type": "approve"},
        ]
    }

    result = await client.post(_url(row), json=body)

    assert result.status_code == 422 and started == []


@pytest.mark.asyncio
async def test_changed_arguments_are_refused_and_retire_the_request(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = approval_payload()
    changed["action_requests"][1]["args"] = {"to": "attacker@example.com"}
    conversation, row = await _request(db, payload=changed)
    started = _started(monkeypatch)

    result = await client.post(_url(row), json=APPROVE)

    assert result.status_code == 409 and started == []
    assert await resume_runs(db, conversation) == []
    await db.refresh(row)
    assert (row.status, row.reason) == ("canceled", "arguments_changed")


@pytest.mark.asyncio
async def test_a_request_whose_run_moved_on_is_retired(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, row = await _request(db)
    run = await db.get(ConversationRun, row.run_id)
    assert run is not None
    run.status = "completed"  # the user typed a new message instead
    await db.commit()
    started = _started(monkeypatch)

    result = await client.post(_url(row), json=APPROVE)

    assert result.status_code == 409 and started == []
    await db.refresh(row)
    assert (row.status, row.reason) == ("canceled", "no_longer_pending")


@pytest.mark.asyncio
async def test_losing_the_membership_while_waiting_refuses_the_resume(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, row = await _request(db)
    started = _started(monkeypatch)
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.user_id == TEST_USER_ID)
        .values(status="removed")
    )
    await db.commit()

    result = await client.post(_url(row), json=APPROVE)

    assert result.status_code in {403, 404} and started == []
    await db.refresh(row)
    assert row.status == "pending"
    assert await resume_runs(db, conversation) == []


@pytest.mark.asyncio
async def test_an_expired_request_cannot_be_decided(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conversation, row = await _request(db)
    row.expires_at = service.now() - timedelta(seconds=1)
    await db.commit()
    started = _started(monkeypatch)

    result = await client.post(_url(row), json=APPROVE)

    assert result.status_code == 409 and result.json()["error"]["code"] == "approval_expired"
    assert started == []


@pytest.mark.asyncio
async def test_the_sweep_ends_an_expired_request_with_a_rejection_and_tells_the_subject(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, row = await _request(db)
    row.expires_at = service.now() - timedelta(seconds=1)
    await db.commit()
    started = _started(monkeypatch)

    assert await inbox.expire_due() == 1

    await db.refresh(row)
    assert (row.status, row.channel, row.reason) == ("expired", "expiry", "approval_expired")
    [resumed] = await resume_runs(db, conversation)
    assert row.resumed_run_id == resumed.id
    [start] = started
    assert [d["type"] for d in start["input_payload"]["decisions"]] == ["reject", "reject"]
    assert start["input_payload"]["decisions"][0]["message"] == inbox.EXPIRED_MESSAGE
    [note] = list(
        await db.scalars(select(Notification).where(Notification.kind == "approval.decided"))
    )
    assert note.user_id == row.subject_user_id and note.payload["status"] == "expired"
    assert await inbox.expire_due() == 0  # handled once


@pytest.mark.asyncio
async def test_an_expired_request_whose_run_cannot_resume_still_expires(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation, row = await _request(db)
    row.expires_at = service.now() - timedelta(seconds=1)
    run = await db.get(ConversationRun, row.run_id)
    assert run is not None
    run.status = "completed"
    await db.commit()
    started = _started(monkeypatch)

    await inbox.expire_due()

    await db.refresh(row)
    assert row.status == "expired" and started == []
    assert await resume_runs(db, conversation) == []


@pytest.mark.asyncio
async def test_the_decider_hears_once_that_a_request_is_about_to_expire(db: AsyncSession) -> None:
    _conversation, row = await _request(db)
    row.expires_at = service.now() + timedelta(minutes=30)
    await db.commit()

    assert await inbox.notify_expiring() == 1
    assert await inbox.notify_expiring() == 0

    [note] = list(
        await db.scalars(select(Notification).where(Notification.kind == "approval.expiring"))
    )
    assert note.user_id == row.decider_user_id and note.target_id == row.id
