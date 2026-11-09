"""Workflow W2a: starting, running, recovering and canceling runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.agent_invoke_runner import StructuredInvokeResult
from app.agent_runtime.structured_output import StructuredOutcome
from app.jobs.context import JobContext
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.workflow import WorkflowRun, WorkflowStepRun
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    ExecutionOwnershipError,
    OwnedExecutionRequest,
    accept_owned_execution,
)
from app.workflows import agent_step, runner
from app.workflows.definition import Clause, Condition
from app.workflows.values import DATA_NOTE, evaluate
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, seed_agent
from tests.sqlite_test_engine import TestSession
from tests.workflow_fixtures import inquiry_workflow

BASE = "/api/workflows"


class Crash(BaseException):
    """A worker dying mid-step (not an ``Exception`` the step would catch)."""


class FakeAgent:
    def __init__(self, *answers: dict[str, Any] | StructuredOutcome) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[Any, str]] = []

    async def __call__(self, cfg: Any, messages: list[dict[str, Any]], **_: Any) -> Any:
        self.calls.append((cfg, messages[0]["content"]))
        answer = self.answers.pop(0)
        if isinstance(answer, StructuredOutcome):
            return StructuredInvokeResult("", answer)
        return StructuredInvokeResult("", StructuredOutcome("ok", answer))


async def _published(client: AsyncClient, db: AsyncSession) -> tuple[str, Any]:
    _, _, agent = await seed_agent(db)
    await db.commit()
    created = (await client.post(BASE, json={"name": "문의 처리"})).json()
    saved = (
        await client.put(
            f"{BASE}/{created['id']}/draft",
            json={"revision": 1, "definition": inquiry_workflow(str(agent.id))},
        )
    ).json()
    published = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved["draft_revision"]}
    )
    assert published.status_code == 201, published.text
    return created["id"], agent


async def _start(client: AsyncClient, workflow_id: str, inquiry: str = "환불해 주세요") -> Any:
    response = await client.post(
        f"{BASE}/{workflow_id}/runs", json={"inputs": {"inquiry": inquiry}}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _job(run_id: str) -> dict[str, Any]:
    async def progress(percent: int, note: str | None) -> None:
        return None

    return await runner.run_job(
        JobContext(
            job_id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            actor_id=TEST_USER_ID,
            type=runner.RUN_JOB,
            attempt=1,
            payload={"run_id": run_id},
            session=TestSession,
            _progress=progress,
        )
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    agent = FakeAgent()
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", agent)
    return agent


@pytest.mark.asyncio
async def test_a_run_goes_start_to_output_through_a_real_agent_step(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, agent = await _published(client, db)
    fake.answers.append({"category": "bug", "urgent": False})
    run = await _start(client, workflow_id, "앱이 꺼져요 <script>")
    assert run["status"] == "queued"
    assert await _job(run["id"]) == {"outcome": "completed"}

    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert detail["status"] == "completed"
    assert detail["output"] == {"category": "bug", "note": "분류: bug"}
    assert [(s["node_id"], s["status"], s["branch"]) for s in detail["steps"]] == [
        ("start", "completed", None),
        ("classify", "completed", None),
        ("is_urgent", "completed", "false"),
        ("draft", "completed", None),
        ("done", "completed", None),
    ]
    (cfg, prompt) = fake.calls[0]
    assert cfg.output_schema["required"] == ["category", "urgent"]
    assert (cfg.run_path, cfg.unattended_approval) == ("workflow", "wait")
    assert prompt.startswith(DATA_NOTE) and "<data>앱이 꺼져요 <script></data>" in prompt

    step = await db.scalar(sa.select(WorkflowStepRun).where(WorkflowStepRun.node_id == "classify"))
    assert step is not None and step.conversation_run_id is not None
    assert step.conversation_id is not None
    conversation_run = await db.get(ConversationRun, step.conversation_run_id)
    assert conversation_run is not None
    assert (conversation_run.run_source, conversation_run.status) == ("WORKFLOW", "completed")
    conversation = await db.get(Conversation, step.conversation_id)
    assert conversation is not None and conversation.source == "workflow"
    assert conversation.agent_id == agent.id
    listed = (await client.get("/api/conversations")).json()
    assert str(step.conversation_id) not in str(listed)  # internal, never listed

    # The envelope binds once: a second acceptance of the same step is refused.
    with pytest.raises(ExecutionOwnershipError):
        await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=step.conversation_id,
                root_agent_id=agent.id,
                context=unit_context(TEST_USER_ID),
                source="workflow",
                input_preview="again",
                envelope=ExecutionEnvelopeRef("workflow", step.id),
            ),
        )
    await db.rollback()
    actions = (
        await db.execute(
            sa.select(AuditEvent.action, AuditEvent.outcome).where(
                AuditEvent.target_id == run["id"]
            )
        )
    ).all()
    assert sorted(actions) == [
        ("workflow.run_finish", "success"),
        ("workflow.run_start", "success"),
    ]


@pytest.mark.asyncio
async def test_an_answer_outside_the_schema_fails_the_run(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, _ = await _published(client, db)
    fake.answers.append(StructuredOutcome("schema_invalid"))
    run = await _start(client, workflow_id)
    assert await _job(run["id"]) == {"outcome": "failed"}
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert (detail["error_code"], detail["steps"][-1]["status"]) == (
        "output_schema_invalid",
        "failed",
    )
    assert [s["node_id"] for s in detail["steps"]] == ["start", "classify"]


async def _waiting_for_approval(client: AsyncClient, db: AsyncSession, fake: FakeAgent) -> Any:
    workflow_id, _ = await _published(client, db)
    fake.answers.append({"category": "refund", "urgent": True})
    run = await _start(client, workflow_id)
    assert await _job(run["id"]) == {"outcome": "waiting"}
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert detail["status"] == "waiting"
    assert (detail["steps"][-1]["node_id"], detail["steps"][-1]["status"]) == ("review", "waiting")
    (request,) = (await client.get("/api/approvals")).json()["items"]
    assert (request["kind"], request["source"], request["conversation_id"]) == (
        "business",
        "workflow",
        None,
    )
    assert request["workflow_run_id"] == run["id"]
    assert request["actions"][0]["args"]["title"] == "긴급 문의: refund"
    assert request["actions"][0]["allowed_decisions"] == ["approve", "reject"]
    return run, request


async def _decide(client: AsyncClient, request: dict[str, Any], decision: str) -> Any:
    return await client.post(
        f"/api/approvals/{request['id']}/decision",
        json={"decisions": [{"type": decision, "message": "확인함"}]},
    )


@pytest.mark.asyncio
async def test_an_approved_request_continues_the_run(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    run, request = await _waiting_for_approval(client, db, fake)
    decided = await _decide(client, request, "approve")
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"
    assert (await _decide(client, request, "reject")).status_code == 409  # decided once
    assert (await client.get(f"/api/workflow-runs/{run['id']}")).json()["status"] == "running"
    assert await _job(run["id"]) == {"outcome": "completed"}
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    review = detail["steps"][3]
    assert (review["branch"], review["output"]) == (
        "approved",
        {"decision": "approved", "comment": "확인함"},
    )
    assert detail["output"] == {"category": "refund", "note": "분류: refund"}
    assert len(fake.calls) == 1  # the agent step is not run again after the wait


@pytest.mark.asyncio
async def test_a_rejected_request_takes_the_rejected_branch(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    run, request = await _waiting_for_approval(client, db, fake)
    assert (await _decide(client, request, "reject")).json()["status"] == "rejected"
    assert await _job(run["id"]) == {"outcome": "failed"}
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert (detail["error_code"], detail["steps"][-1]["node_id"]) == ("workflow_fail_node", "fail")


@pytest.mark.asyncio
async def test_an_expired_request_rejects(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    from app.approvals import inbox
    from app.models.approval_request import ApprovalRequest

    run, request = await _waiting_for_approval(client, db, fake)
    row = await db.get(ApprovalRequest, uuid.UUID(request["id"]))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()
    assert await inbox.expire_due() == 1
    await db.refresh(row)
    assert (row.status, row.channel) == ("expired", "expiry")
    assert await _job(run["id"]) == {"outcome": "failed"}
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert detail["steps"][3]["output"]["decision"] == "expired"


@pytest.mark.asyncio
async def test_canceling_a_waiting_run_cancels_its_request(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    run, request = await _waiting_for_approval(client, db, fake)
    canceled = (await client.post(f"/api/workflow-runs/{run['id']}/cancel")).json()
    assert canceled["status"] == "canceled" and canceled["steps"][-1]["status"] == "canceled"
    assert (await client.get("/api/approvals")).json()["items"] == []
    assert (await _decide(client, request, "approve")).status_code == 409
    assert await _job(run["id"]) == {"outcome": "canceled"}


@pytest.mark.asyncio
async def test_another_person_cannot_decide_a_workflow_request(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    from app.models.approval_request import ApprovalRequest

    _, request = await _waiting_for_approval(client, db, fake)
    row = await db.get(ApprovalRequest, uuid.UUID(request["id"]))
    assert row is not None
    other = uuid.uuid4()
    await seed_agent(db, user_id=other)
    row.decider_user_id = other
    await db.commit()
    assert (await _decide(client, request, "approve")).status_code == 404
    await db.refresh(row)
    assert row.status == "pending"


@pytest.mark.asyncio
async def test_a_step_cut_off_mid_run_is_unknown_and_never_runs_again(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id, _ = await _published(client, db)
    run = await _start(client, workflow_id)

    async def crash(*_: Any) -> Any:
        raise Crash

    real_execute = agent_step.execute
    monkeypatch.setattr(agent_step, "execute", crash)
    with pytest.raises(Crash):
        await _job(run["id"])
    monkeypatch.setattr(agent_step, "execute", real_execute)
    assert await _job(run["id"]) == {"outcome": "unknown"}
    assert fake.calls == []
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert (detail["status"], detail["current_node"]) == ("unknown", "classify")
    assert detail["steps"][-1]["status"] == "unknown"
    assert await _job(run["id"]) == {"outcome": "unknown"}  # a reclaim changes nothing
    # Not finished: the runner is asked once, and the audit says which step and why.
    (request,) = (await client.get("/api/approvals")).json()["items"]
    assert (request["kind"], request["workflow_run_id"]) == ("business", run["id"])
    assert request["actions"][0]["name"] == "workflow_unknown_check"
    assert request["actions"][0]["args"]["node_id"] == "classify"
    finishes = await db.scalars(
        sa.select(AuditEvent).where(AuditEvent.action == "workflow.run_finish")
    )
    assert list(finishes) == []
    (event,) = (
        await db.scalars(sa.select(AuditEvent).where(AuditEvent.action == "workflow.step_unknown"))
    ).all()
    step = await db.scalar(sa.select(WorkflowStepRun).where(WorkflowStepRun.node_id == "classify"))
    assert step is not None
    assert (event.outcome, event.reason_code, event.target_id) == (
        "failure",
        "step_outcome_unknown",
        run["id"],
    )
    metadata = event.event_metadata or {}
    assert metadata["step_id"] == str(step.id)
    assert metadata["conversation_run_id"] == str(step.conversation_run_id)
    assert metadata["node_id"] == "classify"
    assert metadata["approval_request_id"] == request["id"]


@pytest.mark.asyncio
async def test_an_accepted_run_that_failed_fails_the_step(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id, _ = await _published(client, db)
    run = await _start(client, workflow_id)

    async def crash(*_: Any) -> Any:
        raise Crash

    real_execute = agent_step.execute
    monkeypatch.setattr(agent_step, "execute", crash)
    with pytest.raises(Crash):
        await _job(run["id"])
    monkeypatch.setattr(agent_step, "execute", real_execute)
    step = await db.scalar(sa.select(WorkflowStepRun).where(WorkflowStepRun.node_id == "classify"))
    assert step is not None
    conversation_run = await db.get(ConversationRun, step.conversation_run_id)
    assert conversation_run is not None
    conversation_run.status = "failed"
    await db.commit()
    assert await _job(run["id"]) == {"outcome": "failed"}
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert (detail["error_code"], detail["steps"][-1]["status"]) == ("agent_failed", "failed")


@pytest.mark.asyncio
async def test_cancel_between_nodes_stops_before_the_next_one(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, _ = await _published(client, db)
    run = await _start(client, workflow_id)

    async def answer_then_cancel(cfg: Any, messages: list[dict[str, Any]], **_: Any) -> Any:
        async with TestSession() as other:
            stored = await other.get(WorkflowRun, uuid.UUID(run["id"]))
            assert stored is not None
            stored.cancel_requested, stored.status = True, "canceling"
            await other.commit()
        return StructuredInvokeResult(
            "", StructuredOutcome("ok", {"category": "bug", "urgent": False})
        )

    import app.workflows.agent_step as module

    original = module.execute_agent_invoke_structured
    module.execute_agent_invoke_structured = answer_then_cancel  # type: ignore[assignment]
    try:
        assert await _job(run["id"]) == {"outcome": "canceled"}
    finally:
        module.execute_agent_invoke_structured = original
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert [s["node_id"] for s in detail["steps"]] == ["start", "classify"]


@pytest.mark.asyncio
async def test_a_step_envelope_only_accepts_its_own_agent(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, agent = await _published(client, db)
    _, _, other_agent = await seed_agent(db, name="다른 에이전트")
    run = await _start(client, workflow_id)
    stored = await db.get(WorkflowRun, uuid.UUID(run["id"]))
    assert stored is not None
    conversation = Conversation(
        agent_id=other_agent.id,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot="x",
        title="x",
        source="workflow",
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.flush()
    step = WorkflowStepRun(
        run_id=stored.id,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        position=0,
        node_id="classify",
        node_type="agent",
        status="queued",
        agent_id=agent.id,
        conversation_id=conversation.id,
    )
    db.add(step)
    await db.commit()
    with pytest.raises(ExecutionOwnershipError):
        await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=conversation.id,
                root_agent_id=other_agent.id,
                context=unit_context(TEST_USER_ID),
                source="workflow",
                input_preview="x",
                envelope=ExecutionEnvelopeRef("workflow", step.id),
            ),
        )


@pytest.mark.asyncio
async def test_start_checks_inputs_publication_and_changed_agents(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, agent = await _published(client, db)
    for inputs, problem in (
        ({}, "inquiry"),
        ({"inquiry": 3}, "inquiry"),
        ({"inquiry": "x" * 4001}, "inquiry"),
        ({"inquiry": "ok", "extra": 1}, "extra"),
    ):
        response = await client.post(f"{BASE}/{workflow_id}/runs", json={"inputs": inputs})
        assert response.status_code == 422
        names = [p["name"] for p in response.json()["error"]["details"]["problems"]]
        assert problem in names
    draft_only = (await client.post(BASE, json={"name": "초안"})).json()
    unpublished = await client.post(f"{BASE}/{draft_only['id']}/runs", json={"inputs": {}})
    assert unpublished.json()["error"]["code"] == "WORKFLOW_NOT_PUBLISHED"

    agent.system_prompt = "게시 뒤에 바꾼 지시"
    await db.commit()
    changed = await client.post(f"{BASE}/{workflow_id}/runs", json={"inputs": {"inquiry": "x"}})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "WORKFLOW_AGENT_CHANGED"
    assert changed.json()["error"]["details"] == {"node_ids": ["classify"]}
    assert (await db.scalar(sa.select(sa.func.count()).select_from(WorkflowRun))) == 0


@pytest.mark.asyncio
async def test_cancel_before_the_job_runs_nothing(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, _ = await _published(client, db)
    run = await _start(client, workflow_id)
    canceled = await client.post(f"/api/workflow-runs/{run['id']}/cancel")
    assert canceled.json()["status"] == "canceled"
    assert (await client.post(f"/api/workflow-runs/{run['id']}/cancel")).json()["status"] == (
        "canceled"
    )
    assert await _job(run["id"]) == {"outcome": "canceled"}
    assert fake.calls == [] and canceled.json()["steps"] == []
    audits = await db.scalars(
        sa.select(AuditEvent).where(AuditEvent.action == "workflow.run_cancel")
    )
    assert len(list(audits)) == 1


@pytest.mark.asyncio
async def test_another_persons_run_is_not_found(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, _ = await _published(client, db)
    run = await _start(client, workflow_id)
    stored = await db.get(WorkflowRun, uuid.UUID(run["id"]))
    assert stored is not None
    other = uuid.uuid4()
    await seed_agent(db, user_id=other)
    stored.user_id = other
    await db.commit()
    for response in (
        await client.get(f"/api/workflow-runs/{run['id']}"),
        await client.post(f"/api/workflow-runs/{run['id']}/cancel"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKFLOW_RUN_NOT_FOUND"
    assert (await client.get(f"{BASE}/{workflow_id}/runs")).json() == []


def _clause(op: str, value: Any = None, path: str = "/v") -> Condition:
    return Condition(all=[Clause(source="n", path=path, op=op, value=value)])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "op", "operand", "expected"),
    [
        (True, "eq", True, True),
        (1, "eq", True, False),  # a boolean only equals a boolean
        (1, "eq", 1.0, True),
        ("a", "ne", "b", True),
        (5, "gt", 3, True),
        ("5", "gt", 3, False),  # text is not a number
        (None, "lte", 3, False),
        ("환불 요청", "contains", "환불", True),
        (["a", "b"], "contains", "b", True),
        (None, "exists", None, False),
        (0, "exists", None, True),
    ],
)
def test_rule_conditions(value: Any, op: str, operand: Any, expected: bool) -> None:
    decision = evaluate(_clause(op, operand), {"n": {"v": value}})
    assert decision == {"value": expected, "confidence": 1.0, "band": "high", "evaluator": "rule"}


def test_any_and_missing_paths() -> None:
    values = {"n": {"v": 1}}
    either = Condition(
        any=[
            Clause(source="n", path="/missing", op="exists"),
            Clause(source="n", path="/v", op="eq", value=1),
        ]
    )
    assert evaluate(either, values)["value"] is True
    assert evaluate(_clause("eq", 1, "/missing"), values)["value"] is False


@pytest.mark.asyncio
async def test_a_second_job_while_waiting_changes_nothing_and_decide_queues_one(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    from app.models.job import Job

    run, request = await _waiting_for_approval(client, db, fake)
    assert await _job(run["id"]) == {"outcome": "waiting"}
    assert (await client.get(f"/api/workflow-runs/{run['id']}")).json()["status"] == "waiting"
    await _decide(client, request, "approve")
    keys = (
        await db.scalars(sa.select(Job.idempotency_key).where(Job.type == "workflow.run"))
    ).all()
    assert sorted(key or "" for key in keys) == sorted([run["id"], f"{run['id']}:{request['id']}"])


@pytest.mark.asyncio
async def test_concurrent_decisions_land_once(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    from app.approvals.service import ApprovalStale
    from app.models.approval_request import ApprovalRequest
    from app.workflows import approvals

    _, request = await _waiting_for_approval(client, db, fake)
    async with TestSession() as first, TestSession() as second:
        row_a = await first.get(ApprovalRequest, uuid.UUID(request["id"]))
        row_b = await second.get(ApprovalRequest, uuid.UUID(request["id"]))
        assert row_a is not None and row_b is not None
        await approvals.decide(
            first, row_a, decision="approve", comment=None, actor_id=TEST_USER_ID
        )
        await first.commit()
        with pytest.raises(ApprovalStale):
            await approvals.decide(
                second, row_b, decision="reject", comment=None, actor_id=TEST_USER_ID
            )


@pytest.mark.asyncio
async def test_settle_leaves_a_step_that_no_longer_waits(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    from app.models.approval_request import ApprovalRequest
    from app.workflows import approvals

    run, request = await _waiting_for_approval(client, db, fake)
    step = await db.scalar(sa.select(WorkflowStepRun).where(WorkflowStepRun.node_id == "review"))
    row = await db.get(ApprovalRequest, uuid.UUID(request["id"]))
    assert step is not None and row is not None
    step.status = "canceled"
    await db.commit()
    await approvals.settle(db, row, decision="approve", comment=None)
    await db.refresh(step)
    assert (step.status, step.branch) == ("canceled", None)


@pytest.mark.asyncio
async def test_a_request_of_another_organization_is_not_found(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    from app.models.approval_request import ApprovalRequest

    _, request = await _waiting_for_approval(client, db, fake)
    row = await db.get(ApprovalRequest, uuid.UUID(request["id"]))
    assert row is not None
    row.org_id = uuid.uuid4()
    await db.commit()
    assert (await _decide(client, request, "approve")).status_code == 404


# --- W4: the unknown-step check, agents rechecked at each step -----------------------


async def _unknown(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any]:
    workflow_id, _ = await _published(client, db)
    run = await _start(client, workflow_id)

    async def crash(*_: Any) -> Any:
        raise Crash

    real_execute = agent_step.execute
    monkeypatch.setattr(agent_step, "execute", crash)
    with pytest.raises(Crash):
        await _job(run["id"])
    monkeypatch.setattr(agent_step, "execute", real_execute)
    assert await _job(run["id"]) == {"outcome": "unknown"}
    (request,) = (await client.get("/api/approvals")).json()["items"]
    return run, request


@pytest.mark.asyncio
async def test_approving_an_unknown_step_runs_it_again_in_a_new_conversation(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, request = await _unknown(client, db, fake, monkeypatch)
    classify = sa.select(WorkflowStepRun).where(WorkflowStepRun.node_id == "classify")
    before = await db.scalar(classify)
    assert before is not None
    first_conversation, first_run = before.conversation_id, before.conversation_run_id
    assert (await _decide(client, request, "approve")).json()["status"] == "approved"
    assert (await _decide(client, request, "approve")).status_code == 409  # once
    assert (await client.get(f"/api/workflow-runs/{run['id']}")).json()["status"] == "running"
    fake.answers.append({"category": "bug", "urgent": False})
    assert await _job(run["id"]) == {"outcome": "completed"}
    assert len(fake.calls) == 1
    db.expire_all()
    step = await db.scalar(classify)
    assert step is not None and step.status == "completed"
    assert step.conversation_id not in (None, first_conversation)
    assert step.conversation_run_id not in (None, first_run)
    assert (step.input or {})["previous_attempts"] == [
        {
            "conversation_id": str(first_conversation),
            "conversation_run_id": str(first_run),
            "approval_request_id": request["id"],
        }
    ]
    finishes = (
        await db.scalars(sa.select(AuditEvent).where(AuditEvent.action == "workflow.run_finish"))
    ).all()
    assert [(e.outcome, (e.event_metadata or {})["status"]) for e in finishes] == [
        ("success", "completed")
    ]


@pytest.mark.asyncio
async def test_rejecting_or_not_answering_an_unknown_step_fails_the_run(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.approvals import inbox
    from app.models.approval_request import ApprovalRequest

    run, request = await _unknown(client, db, fake, monkeypatch)
    assert (await _decide(client, request, "reject")).json()["status"] == "rejected"
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert (detail["status"], detail["error_code"]) == ("failed", "step_outcome_unknown")
    assert detail["steps"][-1]["status"] == "unknown"  # what happened stays on record
    assert await _job(run["id"]) == {"outcome": "failed"}
    assert fake.calls == []
    (event,) = (
        await db.scalars(sa.select(AuditEvent).where(AuditEvent.action == "workflow.run_finish"))
    ).all()
    assert (event.outcome, event.reason_code) == ("failure", "step_outcome_unknown")

    second, pending = await _unknown(client, db, fake, monkeypatch)
    row = await db.get(ApprovalRequest, uuid.UUID(pending["id"]))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()
    assert await inbox.expire_due() == 1
    detail = (await client.get(f"/api/workflow-runs/{second['id']}")).json()
    assert (detail["status"], detail["error_code"]) == ("failed", "step_outcome_unknown")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_canceling_an_unknown_run_ends_it_and_its_check(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, request = await _unknown(client, db, fake, monkeypatch)
    canceled = (await client.post(f"/api/workflow-runs/{run['id']}/cancel")).json()
    assert canceled["status"] == "canceled"
    assert (await client.get("/api/approvals")).json()["items"] == []
    assert (await _decide(client, request, "approve")).status_code == 409
    assert await _job(run["id"]) == {"outcome": "canceled"}
    assert fake.calls == []


@pytest.mark.asyncio
async def test_an_agent_archived_after_the_start_is_not_run(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, agent = await _published(client, db)
    run = await _start(client, workflow_id)
    agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    assert await _job(run["id"]) == {"outcome": "failed"}
    assert fake.calls == []
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert (detail["error_code"], detail["steps"][-1]["status"]) == ("agent_archived", "failed")
    # No step was accepted: no internal conversation run was created for it.
    assert detail["steps"][-1]["conversation_id"] is None


@pytest.mark.asyncio
async def test_an_agent_no_longer_runnable_during_an_approval_wait_is_not_run(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    """A second agent step after the approval: losing the agent while waiting stops it."""
    from tests.workflow_fixtures import CLASSIFY_SCHEMA, node, ref, text

    _, _, agent = await seed_agent(db)
    await db.commit()
    definition = inquiry_workflow(str(agent.id))
    definition["nodes"].append(
        {
            "id": "answer",
            "type": "agent",
            "agent_id": str(agent.id),
            "prompt": [text("답하세요: "), ref("input", "/inquiry")],
            "output_schema": CLASSIFY_SCHEMA,
        }
    )
    definition["edges"] = [
        edge for edge in definition["edges"] if edge.get("when") != "approved"
    ] + [
        {"from": "review", "to": "answer", "when": "approved"},
        {"from": "answer", "to": "draft"},
    ]
    node(definition, "draft")["template"] = [text("분류: "), ref("classify", "/category")]
    created = (await client.post(BASE, json={"name": "두 단계"})).json()
    saved = (
        await client.put(
            f"{BASE}/{created['id']}/draft", json={"revision": 1, "definition": definition}
        )
    ).json()
    published = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved["draft_revision"]}
    )
    assert published.status_code == 201, published.text
    fake.answers.append({"category": "refund", "urgent": True})
    run = await _start(client, created["id"])
    assert await _job(run["id"]) == {"outcome": "waiting"}
    # The agent leaves the runner's reach while the run waits (legacy mode: ownership).
    agent.user_id = uuid.uuid4()
    await db.commit()
    (request,) = (await client.get("/api/approvals")).json()["items"]
    assert (await _decide(client, request, "approve")).status_code == 200
    assert await _job(run["id"]) == {"outcome": "failed"}
    assert len(fake.calls) == 1  # only the first step ran
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert (detail["error_code"], detail["steps"][-1]["node_id"]) == ("agent_missing", "answer")


@pytest.mark.asyncio
async def test_a_worker_dying_after_a_stored_step_resumes_at_the_next_node(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completion criterion 2: a new worker continues from the step rows, once each."""
    workflow_id, _ = await _published(client, db)
    fake.answers.append({"category": "bug", "urgent": False})
    run = await _start(client, workflow_id)
    real_decision = runner._decision_branch

    async def dies_at_the_decision(*args: Any) -> None:
        raise Crash  # the agent step is stored; the decision is not

    monkeypatch.setattr(runner, "_decision_branch", dies_at_the_decision)
    with pytest.raises(Crash):
        await _job(run["id"])
    db.expire_all()
    steps = (await client.get(f"/api/workflow-runs/{run['id']}")).json()["steps"]
    assert [(s["node_id"], s["status"]) for s in steps] == [
        ("start", "completed"),
        ("classify", "completed"),
    ]
    monkeypatch.setattr(runner, "_decision_branch", real_decision)
    assert await _job(run["id"]) == {"outcome": "completed"}  # the next worker
    assert len(fake.calls) == 1  # the agent step ran once
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert [s["node_id"] for s in detail["steps"]] == [
        "start",
        "classify",
        "is_urgent",
        "draft",
        "done",
    ]


@pytest.mark.asyncio
async def test_an_emergency_stop_of_the_agent_stops_the_next_agent_step(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id, agent = await _published(client, db)
    run = await _start(client, workflow_id)
    stopped = await client.post(
        "/api/emergency-stops",
        json={"scope_type": "agent", "scope_id": str(agent.id), "reason": "오발송 의심"},
    )
    assert stopped.status_code in (200, 201), stopped.text
    assert await _job(run["id"]) == {"outcome": "failed"}
    assert fake.calls == []
    detail = (await client.get(f"/api/workflow-runs/{run['id']}")).json()
    assert (detail["error_code"], detail["steps"][-1]["node_id"]) == ("emergency_stop", "classify")
