"""Workflow W2c: a tool approval inside an agent step waits, then the step resumes."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.structured_output import OUTPUT_TOOL, structured_outcome
from app.approvals import recording
from app.approvals.service import PendingInterrupt
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import ConversationRun
from app.models.job import Job
from app.models.workflow import WorkflowRun, WorkflowStepRun
from app.workflows import agent_step, tool_waits
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.test_workflow_runs import FakeAgent, _job, _published, _start
from tests.workflow_fixtures import CLASSIFY_SCHEMA

MAIL = {
    "action_requests": [{"name": "send_mail", "args": {"to": "a@example.com"}}],
    "review_configs": [{"action_name": "send_mail", "allowed_decisions": ["approve", "reject"]}],
}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    agent = FakeAgent()
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", agent)
    return agent


@pytest.fixture
def paused(monkeypatch: pytest.MonkeyPatch) -> list[PendingInterrupt]:
    """The step's graph stops before ``send_mail`` (what the checkpoint would hold)."""
    interrupts = [PendingInterrupt("int-1", MAIL)]

    async def checkpoint(_: uuid.UUID) -> list[PendingInterrupt]:
        return list(interrupts)

    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    return interrupts


async def _waiting_step(client: AsyncClient, db: AsyncSession, fake: FakeAgent) -> Any:
    workflow_id, _ = await _published(client, db)
    fake.answers.append({"category": "bug", "urgent": False})  # never read: the graph paused
    run = await _start(client, workflow_id)
    assert await _job(run["id"]) == {"outcome": "waiting"}
    step = await db.scalar(sa.select(WorkflowStepRun).where(WorkflowStepRun.node_id == "classify"))
    assert step is not None
    return run, step


@pytest.mark.asyncio
async def test_an_approval_inside_a_step_waits_with_the_step_on_the_request(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, paused: list[PendingInterrupt]
) -> None:
    run, step = await _waiting_step(client, db, fake)
    cfg, _ = fake.calls[0]
    assert cfg.unattended_approval == "wait"
    assert step.status == "waiting"
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert detail["status"] == "waiting"
    conversation_run = await db.get(ConversationRun, step.conversation_run_id)
    assert conversation_run is not None and conversation_run.status == "interrupted"
    (request,) = (await client.get("/api/approvals")).json()["items"]
    assert (request["kind"], request["source"], request["workflow_run_id"]) == (
        "tool",
        "workflow",
        run["id"],
    )
    row = await db.get(ApprovalRequest, uuid.UUID(request["id"]))
    assert row is not None and row.workflow_step_run_id == step.id
    assert await _job(run["id"]) == {"outcome": "waiting"}  # a reclaim changes nothing


async def _resumed(db: AsyncSession, step: WorkflowStepRun, status: str) -> ConversationRun:
    """The run the chat resume path accepted for the decision (parent = the step's run)."""
    origin = await db.get(ConversationRun, step.conversation_run_id)
    assert origin is not None
    resumed = ConversationRun(
        conversation_id=origin.conversation_id,
        agent_id=origin.agent_id,
        user_id=origin.user_id,
        org_id=origin.org_id,
        tenant_id=origin.tenant_id,
        parent_run_id=origin.id,
        source="resume",
        status=status,
        run_source="CHAT",
    )
    db.add(resumed)
    await db.commit()
    return resumed


def _answer(args: dict[str, Any]) -> dict[str, Any]:
    call = AIMessage(content="", tool_calls=[{"name": OUTPUT_TOOL, "args": args, "id": "c9"}])
    return {"messages": [call, ToolMessage(content="ok", tool_call_id="c9")]}


@pytest.mark.asyncio
async def test_the_resumed_step_answers_in_its_schema_and_the_run_continues(
    client: AsyncClient,
    db: AsyncSession,
    fake: FakeAgent,
    paused: list[PendingInterrupt],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, step = await _waiting_step(client, db, fake)
    origin = await db.get(ConversationRun, step.conversation_run_id)
    assert origin is not None

    # The chat resume path rebuilds the config from the agent alone: the hook adds the schema.
    cfg = SimpleNamespace(output_schema=None, unattended_approval=None)
    await tool_waits.adapt_resume(db, origin, cfg)  # type: ignore[arg-type]
    assert (cfg.output_schema, cfg.unattended_approval) == (CLASSIFY_SCHEMA, "wait")

    resumed = await _resumed(db, step, "completed")
    chained = SimpleNamespace(output_schema=None, unattended_approval=None)
    await tool_waits.adapt_resume(db, resumed, chained)  # type: ignore[arg-type]
    assert chained.output_schema == CLASSIFY_SCHEMA  # a second wait in the chain too

    async def answer(*_: Any) -> Any:
        return structured_outcome(
            _answer({"category": "bug", "urgent": False, "x": "sk-live-secret-1"}), CLASSIFY_SCHEMA
        )

    monkeypatch.setattr(tool_waits, "_answer", answer)
    await tool_waits.settle(resumed.id, "completed", unit_context(TEST_USER_ID))
    await db.refresh(step)
    assert step.status == "failed" and step.error_code == "output_schema_invalid"  # extra key


@pytest.mark.asyncio
async def test_a_valid_resumed_answer_completes_the_run(
    client: AsyncClient,
    db: AsyncSession,
    fake: FakeAgent,
    paused: list[PendingInterrupt],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, step = await _waiting_step(client, db, fake)
    resumed = await _resumed(db, step, "completed")

    async def answer(*_: Any) -> Any:
        return structured_outcome(_answer({"category": "bug", "urgent": False}), CLASSIFY_SCHEMA)

    monkeypatch.setattr(tool_waits, "_answer", answer)
    await tool_waits.settle(resumed.id, "interrupted", unit_context(TEST_USER_ID))
    await db.refresh(step)
    assert step.status == "waiting"  # waiting again: nothing moves
    await tool_waits.settle(resumed.id, "completed", unit_context(TEST_USER_ID))
    await db.refresh(step)
    assert (step.status, step.output) == ("completed", {"category": "bug", "urgent": False})
    stored = await db.get(WorkflowRun, uuid.UUID(run["id"]))
    assert stored is not None
    await db.refresh(stored)
    assert stored.status == "running"
    keys = (
        await db.scalars(sa.select(Job.idempotency_key).where(Job.type == "workflow.run"))
    ).all()
    assert f"{run['id']}:{resumed.id}" in keys
    assert await _job(run["id"]) == {"outcome": "completed"}
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert detail["output"] == {"category": "bug", "note": "분류: bug"}
    assert len(fake.calls) == 1  # the agent was not invoked again
    await tool_waits.settle(resumed.id, "completed", unit_context(TEST_USER_ID))  # idempotent
    await db.refresh(step)
    assert step.status == "completed"


@pytest.mark.asyncio
async def test_a_failed_resume_fails_the_step(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, paused: list[PendingInterrupt]
) -> None:
    run, step = await _waiting_step(client, db, fake)
    resumed = await _resumed(db, step, "failed")
    await tool_waits.settle(resumed.id, "failed", unit_context(TEST_USER_ID))
    assert await _job(run["id"]) == {"outcome": "failed"}
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert detail["error_code"] == "agent_failed"


@pytest.mark.asyncio
async def test_requests_retired_without_a_decision_fail_the_step(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, paused: list[PendingInterrupt]
) -> None:
    run, step = await _waiting_step(client, db, fake)
    row = await db.scalar(sa.select(ApprovalRequest))
    assert row is not None
    row.status, row.reason = "canceled", "emergency_stop"
    await db.commit()
    await tool_waits.abandon(unit_context(TEST_USER_ID), [row.id])
    await db.refresh(step)
    assert (step.status, step.error_code) == ("failed", "emergency_stop")
    assert await _job(run["id"]) == {"outcome": "failed"}


@pytest.mark.asyncio
async def test_settle_ignores_runs_that_are_not_workflow_steps(db: AsyncSession) -> None:
    await tool_waits.settle(uuid.uuid4(), "completed", unit_context(TEST_USER_ID))


def test_the_answer_is_read_from_the_last_model_turn_only() -> None:
    valid = {"category": "bug", "urgent": False}  # valid, but not the last model turn
    stale = AIMessage(content="", tool_calls=[{"name": OUTPUT_TOOL, "args": valid, "id": "a"}])
    later = AIMessage(content="다른 답")
    assert structured_outcome({"messages": [stale, later]}, CLASSIFY_SCHEMA).status == (
        "schema_invalid"
    )
    ok = structured_outcome(_answer({"category": "other", "urgent": True}), CLASSIFY_SCHEMA)
    assert (ok.status, ok.value) == ("ok", {"category": "other", "urgent": True})


def test_the_chat_resume_path_adapts_workflow_steps_before_accepting() -> None:
    import ast
    import inspect

    from app.routers import conversation_protocol_fork

    tree = ast.parse(inspect.getsource(conversation_protocol_fork._handle_input_respond_command))

    def line(name: str) -> int:
        return min(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == name
        )

    assert line("origin_run_path") < line("adapt_workflow_resume") < line("accept_owned_execution")


@pytest.mark.asyncio
async def test_a_step_keeps_waiting_while_another_request_is_pending(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, paused: list[PendingInterrupt]
) -> None:
    run, step = await _waiting_step(client, db, fake)
    row = await db.scalar(sa.select(ApprovalRequest))
    assert row is not None
    other = ApprovalRequest(
        **{
            column.name: getattr(row, column.name)
            for column in ApprovalRequest.__table__.columns
            if column.name not in ("id", "interrupt_id", "created_at", "updated_at")
        },
        interrupt_id="int-2",
    )
    db.add(other)
    row.status = "canceled"
    await db.commit()
    await tool_waits.abandon(unit_context(TEST_USER_ID), [row.id])
    await db.refresh(step)
    assert step.status == "waiting"


@pytest.mark.asyncio
async def test_a_later_wait_in_the_chain_inherits_the_step(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, paused: list[PendingInterrupt]
) -> None:
    from app.approvals.service import RunOrigin, record_pending

    _, step = await _waiting_step(client, db, fake)
    first = await db.scalar(sa.select(ApprovalRequest))
    resumed = await _resumed(db, step, "interrupted")
    assert first is not None
    first.resumed_run_id = resumed.id
    await db.commit()
    (later,) = await record_pending(
        db, resumed, [PendingInterrupt("int-9", MAIL)], origin=RunOrigin()
    )
    assert (later.source, later.workflow_run_id, later.workflow_step_run_id) == (
        "workflow",
        step.run_id,
        step.id,
    )
