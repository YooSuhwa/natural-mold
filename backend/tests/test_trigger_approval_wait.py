"""A scheduled run that waits for the approval inbox and ends with its resume (17.3)."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.approvals import recording, service, triggers
from app.authz.org_context import OrgContext
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import ConversationRun
from app.models.notification import Notification
from tests.authz.g03_support import approval_payload
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_trigger_executor import _seed_full_setup

CONTEXT = OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)


async def _paused_schedule_run(monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    trigger_id, _ = await _seed_full_setup(
        with_tools=True, tool_definition_key="gmail_send", tool_name="Gmail Send"
    )

    async def paused(_conversation_id: uuid.UUID) -> list[service.PendingInterrupt]:
        return [service.PendingInterrupt("intr-t", approval_payload())]

    monkeypatch.setattr(recording, "checkpoint_interrupts", paused)
    with (
        patch(
            "app.agent_runtime.trigger_executor.execute_agent_invoke",
            return_value="메일을 보내려면 승인이 필요합니다",
        ),
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))
    return trigger_id


async def _rows(trigger_id: uuid.UUID) -> tuple[AgentTrigger, AgentTriggerRun, Any, Any]:
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        [run] = list(
            await db.scalars(
                select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == trigger_id)
            )
        )
        conversation_run = await db.get(ConversationRun, run.conversation_run_id)
        approvals = list(await db.scalars(select(ApprovalRequest)))
        assert trigger is not None
        return trigger, run, conversation_run, approvals


@pytest.mark.asyncio
async def test_a_paused_schedule_run_waits_for_the_inbox(monkeypatch: pytest.MonkeyPatch) -> None:
    trigger_id = await _paused_schedule_run(monkeypatch)

    trigger, run, conversation_run, approvals = await _rows(trigger_id)
    assert (run.status, run.output_preview, run.finished_at) == ("waiting", "승인 대기 중", None)
    assert trigger.last_status == "waiting"
    assert trigger.failure_count == 0 and trigger.run_count == 0  # neither, yet
    # The run released its slot: interrupted, only the checkpoint remains.
    assert (conversation_run.status, conversation_run.is_active) == ("interrupted", False)
    assert conversation_run.interrupt_id == "intr-t"
    [row] = approvals
    assert (row.source, row.trigger_id, row.trigger_run_id) == ("trigger", trigger_id, run.id)
    assert row.run_id == conversation_run.id and row.agent_name == "Trigger Agent"
    async with TestSession() as db:
        [note] = list(
            await db.scalars(select(Notification).where(Notification.kind == "approval.requested"))
        )
    assert note.user_id == TEST_USER_ID and note.payload["source"] == "trigger"


@pytest.mark.asyncio
async def test_a_waiting_run_holds_the_next_firing_back(monkeypatch: pytest.MonkeyPatch) -> None:
    trigger_id = await _paused_schedule_run(monkeypatch)

    with (
        patch("app.agent_runtime.trigger_executor.execute_agent_invoke") as invoke,
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        assert await execute_trigger(str(trigger_id)) is None

    invoke.assert_not_called()


async def _resume(row: ApprovalRequest, *decisions: str, channel: str = "inbox") -> uuid.UUID:
    async with TestSession() as db:
        parent = await db.get(ConversationRun, row.run_id)
        assert parent is not None
        child = ConversationRun(
            conversation_id=parent.conversation_id,
            agent_id=parent.agent_id,
            user_id=parent.user_id,
            parent_run_id=parent.id,
            source="resume",
            status="completed",
            is_active=False,
            tenant_id=parent.tenant_id,
            org_id=parent.org_id,
        )
        db.add(child)
        await db.flush()
        await service.claim(
            db,
            service.Claim(
                parent.id,
                {row.interrupt_id: list(decisions)},
                channel,
                None if channel == "expiry" else TEST_USER_ID,
                resumed_run_id=child.id,
            ),
        )
        await db.commit()
        return child.id


@pytest.mark.asyncio
async def test_the_resumed_run_ends_the_schedule_run(monkeypatch: pytest.MonkeyPatch) -> None:
    trigger_id = await _paused_schedule_run(monkeypatch)
    *_, [row] = await _rows(trigger_id)
    child = await _resume(row, "approve", "approve")

    await triggers.settle(child, "interrupted", CONTEXT)  # waiting again: no change
    assert (await _rows(trigger_id))[1].status == "waiting"
    await triggers.settle(child, "completed", CONTEXT)

    trigger, run, *_ = await _rows(trigger_id)
    assert (run.status, trigger.last_status, trigger.run_count) == ("success", "success", 1)
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_an_expired_request_fails_the_schedule_run(monkeypatch: pytest.MonkeyPatch) -> None:
    trigger_id = await _paused_schedule_run(monkeypatch)
    *_, [row] = await _rows(trigger_id)
    child = await _resume(row, "reject", "reject", channel="expiry")

    await triggers.settle(child, "completed", CONTEXT)

    trigger, run, *_ = await _rows(trigger_id)
    assert (run.status, run.reason_code) == ("failed", "approval_expired")
    assert trigger.failure_count == 1


@pytest.mark.asyncio
async def test_a_blocked_schedule_never_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    trigger_id, _ = await _seed_full_setup(
        with_tools=True, tool_definition_key="gmail_send", tool_name="Gmail Send"
    )
    async with TestSession() as db:
        trigger = await db.get(AgentTrigger, trigger_id)
        assert trigger is not None
        trigger.approval_policy = "blocked"
        await db.commit()

    async def paused(_conversation_id: uuid.UUID) -> list[service.PendingInterrupt]:
        raise AssertionError("a blocked schedule does not look for a pause")

    monkeypatch.setattr(recording, "checkpoint_interrupts", paused)
    with (
        patch("app.agent_runtime.trigger_executor.execute_agent_invoke", return_value="ok"),
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))

    _trigger, run, _conversation_run, approvals = await _rows(trigger_id)
    assert run.status == "success" and approvals == []


@pytest.mark.asyncio
async def test_a_request_retired_without_a_decision_ends_the_schedule_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing resumes a canceled request, so the run must not wait (and hold the schedule)."""
    from app.approvals import inbox

    trigger_id = await _paused_schedule_run(monkeypatch)
    *_, [row] = await _rows(trigger_id)

    async with TestSession() as db:
        await inbox.close(db, row, "arguments_changed")

    trigger, run, *_ = await _rows(trigger_id)
    assert (run.status, run.reason_code) == ("failed", "arguments_changed")
    assert trigger.failure_count == 1 and run.finished_at is not None
