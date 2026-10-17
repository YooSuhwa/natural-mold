"""G-06: resume decisions are checked on the server before the graph (plan 11.5).

Done-when: decisions outside the pending interrupt's allow-list are refused with a
typed 4xx and no resume run (protocol ``input.respond`` and REST ``/messages/resume``);
allowed decisions — including a name-less ``edit`` (edit-by-index) and the AD-4
``scope: "session"`` approve — still pass.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.conversation_resume_decisions import decision_problem
from tests.authz.g03_support import (
    approval_payload,
    auth_payload,
    commands_url,
    respond_command,
    resume_runs,
    seed_conversation,
    seed_interrupted,
)

AUTH, HITL = auth_payload(attempt=2), approval_payload()


def _d(*decisions: dict[str, Any]) -> dict[str, Any]:
    return {"decisions": list(decisions)}


@pytest.mark.parametrize(
    "response",
    [
        _d({"type": "respond", "message": "retry"}),
        {"type": "respond", "message": "cancel"},
        _d({"type": "respond", "message": "skip", "attempt": 2}),
    ],
)
def test_auth_required_allows_respond_retry_skip_cancel(response: Any) -> None:
    assert decision_problem(response, AUTH) is None


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_d({"type": "approve"}), "INVALID_RESUME_DECISION"),
        (_d({"type": "respond", "message": "connect-everything"}), "INVALID_RESUME_DECISION"),
        (
            _d({"type": "respond", "message": "retry"}, {"type": "respond", "message": "skip"}),
            "INVALID_RESUME_DECISION",
        ),
        (_d({"type": "respond", "message": "retry", "args": {}}), "INVALID_RESUME_DECISION"),
        (_d({"type": "respond", "message": "retry", "attempt": 1}), "STALE_INTERRUPT"),
        ("retry", "INVALID_RESUME_DECISION"),
    ],
)
def test_auth_required_refuses_decisions_outside_the_allow_list(response: Any, code: str) -> None:
    problem = decision_problem(response, AUTH)
    assert problem is not None and problem[0] == code
    assert problem[2] == (409 if code == "STALE_INTERRUPT" else 422)


@pytest.mark.parametrize(
    "response",
    [
        _d({"type": "approve"}, {"type": "approve"}),
        _d({"type": "reject", "message": "no"}, {"type": "edit", "edited_action": {"args": {}}}),
        _d({"type": "approve", "scope": "session"}, {"type": "reject"}),
    ],
)
def test_approval_allows_each_actions_whitelist_and_name_less_edit(response: Any) -> None:
    assert decision_problem(response, HITL) is None


@pytest.mark.parametrize(
    "response",
    [
        _d({"type": "edit", "edited_action": {"args": {}}}, {"type": "approve"}),  # skill: no edit
        _d({"type": "respond", "message": "x"}, {"type": "approve"}),
        _d({"type": "approve"}),  # count mismatch
        _d({"type": "approve"}, {"type": "approve"}, {"type": "approve"}),
        _d({"type": "approve"}, {"type": "edit"}),  # edit without edited_action.args
        _d({"type": "approve", "tool": "x"}, {"type": "approve"}),
        _d({"type": "reject", "scope": "session"}, {"type": "approve"}),
        {"type": "approve"},  # not the decisions envelope
    ],
)
def test_approval_refuses_decisions_outside_the_allow_list(response: Any) -> None:
    problem = decision_problem(response, HITL)
    assert problem is not None and problem[:1] == ("INVALID_RESUME_DECISION",)


def test_unreadable_review_config_fails_closed_and_other_interrupts_pass() -> None:
    broken = {"action_requests": [{"name": "a"}], "review_configs": [{"action_name": "a"}]}
    assert decision_problem(_d({"type": "approve"}), broken) is not None
    assert decision_problem({"answer": "x"}, {"type": "ask_user", "question": "?"}) is None


def _no_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(**_kwargs: Any) -> None:
        raise AssertionError("a refused resume must not start a run")

    monkeypatch.setattr("app.routers.conversation_agent_protocol.start_conversation_run", fail)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "response", "status", "code"),
    [
        (
            HITL,
            _d({"type": "edit", "edited_action": {"args": {}}}, {"type": "approve"}),
            422,
            "INVALID_RESUME_DECISION",
        ),
        (AUTH, _d({"type": "approve"}), 422, "INVALID_RESUME_DECISION"),
        (AUTH, _d({"type": "respond", "message": "retry", "attempt": 1}), 409, "STALE_INTERRUPT"),
    ],
)
async def test_input_respond_refuses_before_any_resume_run(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    response: Any,
    status: int,
    code: str,
) -> None:
    conversation = await seed_conversation(db)
    await seed_interrupted(db, conversation, payload)
    _no_worker(monkeypatch)

    result = await client.post(commands_url(conversation), json=respond_command(response))

    assert result.status_code == status
    assert result.json()["error"]["code"] == code
    assert await resume_runs(db, conversation) == []


@pytest.mark.asyncio
async def test_rest_resume_refuses_a_decision_outside_the_allow_list(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = await seed_conversation(db)
    await seed_interrupted(db, conversation, approval_payload())

    async def fail(**_kwargs: Any) -> None:
        raise AssertionError("a refused resume must not start a run")

    monkeypatch.setattr("app.routers.chat_resume_entries.start_conversation_run", fail)
    body = _d({"type": "edit", "edited_action": {"args": {}}}, {"type": "approve"})
    result = await client.post(f"/api/conversations/{conversation.id}/messages/resume", json=body)

    assert result.status_code == 422
    assert result.json()["error"]["code"] == "INVALID_RESUME_DECISION"
    assert await resume_runs(db, conversation) == []


@pytest.mark.asyncio
async def test_rest_resume_stale_checks_the_auth_attempt(
    client: AsyncClient, db: AsyncSession
) -> None:
    conversation = await seed_conversation(db)
    await seed_interrupted(db, conversation, auth_payload(1), auth_payload(2))
    body = _d({"type": "respond", "message": "retry", "attempt": 1})
    result = await client.post(f"/api/conversations/{conversation.id}/messages/resume", json=body)

    assert result.status_code == 409
    assert result.json()["error"]["code"] == "STALE_INTERRUPT"
    assert await resume_runs(db, conversation) == []


@pytest.mark.asyncio
async def test_rest_resume_checks_a_legacy_trace_interrupt_too(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent_runtime import event_names
    from app.models.message_event import MessageEvent

    conversation = await seed_conversation(db)  # no interrupted run row: legacy trace only
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id="legacy-msg",
            events=[{"event": event_names.INTERRUPT, "data": {}}],
            status="completed",
        )
    )
    await db.commit()

    async def checkpoint(_config: Any, *_saver: Any) -> list[dict[str, Any]]:
        return [{"interrupt_id": "cp-1", "payload": approval_payload(), "namespace": []}]

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_interrupts.interrupt_payloads_from_checkpointer",
        checkpoint,
    )
    body = _d({"type": "edit", "edited_action": {"args": {}}}, {"type": "approve"})
    result = await client.post(f"/api/conversations/{conversation.id}/messages/resume", json=body)

    assert result.status_code == 422
    assert result.json()["error"]["code"] == "INVALID_RESUME_DECISION"
    assert await resume_runs(db, conversation) == []
