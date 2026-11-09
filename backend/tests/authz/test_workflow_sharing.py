"""Sharing workflows and naming approvers (workflow-starts-sharing-plan 3.3-3.4).

The owner is another member; the client is the caller. Legacy keeps the owner-only answer;
enforce lets a caller with ``workflow#can_run`` see and run it (as itself), and only
``can_edit`` changes the draft. Runs stay the runner's own. A named approver must be able to
run the workflow; one who lost access hands the decision back to the runner.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.fake import FakeAuthz
from app.authz.runtime import legacy_service
from app.authz.service import AuthzService
from app.models.approval_request import ApprovalRequest
from app.models.authz import ResourceGrant
from app.models.workflow import Workflow
from app.workflows import agent_step, runner, service
from tests.authz.d04_support import OTHER_USER_ID
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, seed
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID
from tests.test_workflow_runs import FakeAgent, _job
from tests.workflow_fixtures import inquiry_workflow

OWNER, CALLER = OTHER_USER_ID, TEST_USER_ID


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)
    await add_member(db, THIRD_USER_ID)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    agent = FakeAgent()
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", agent)
    return agent


def _enforce(monkeypatch: pytest.MonkeyPatch, *allowed: tuple[str, str, str]) -> None:
    authz = AuthzService(FakeAuthz(frozenset(allowed)), mode="enforce")
    monkeypatch.setattr(runtime, "_current", authz)


async def _owners_workflow(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, approver: dict[str, Any] | None = None
) -> tuple[Workflow, Any]:
    """The owner's published inquiry workflow, made as the owner (legacy mode, or with
    the named approver allowed to run it so publishing passes)."""
    monkeypatch.setattr(runtime, "_current", legacy_service())
    agent = await add_agent(db, owner=OWNER)
    owner = unit_context(OWNER)
    workflow = await service.create_workflow(db, owner, name="공유 문의 처리", description=None)
    definition = inquiry_workflow(str(agent.id))
    if approver is not None:
        for node in definition["nodes"]:
            if node["type"] == "approval":
                node["approver"] = approver
    await service.save_draft(db, owner, workflow.id, revision=1, definition=definition)
    await db.commit()
    if approver is not None:
        _enforce(
            monkeypatch,
            *_runs_own(workflow, agent),
            (f"user:{approver['user_id']}", "can_run", f"workflow:{workflow.id}"),
        )
    await service.publish(db, owner, workflow.id, revision=2)
    await db.commit()
    monkeypatch.setattr(runtime, "_current", legacy_service())
    return workflow, agent


def _runs_own(workflow: Workflow, agent: Any) -> tuple[tuple[str, str, str], ...]:
    """What the owner holds in enforce mode: its workflow (run, edit) and its agent."""
    owner = f"user:{OWNER}"
    return (
        (owner, "can_run", f"workflow:{workflow.id}"),
        (owner, "can_edit", f"workflow:{workflow.id}"),
        (owner, "can_run", f"agent:{agent.id}"),
        (owner, "can_use", f"llm_model:{agent.model_id}"),
    )


def _grant(workflow_id: uuid.UUID, user_id: uuid.UUID, relation: str = "consumer") -> ResourceGrant:
    return ResourceGrant(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        resource_type="workflow",
        resource_id=workflow_id,
        relation=relation,
        principal_type="user",
        principal_id=user_id,
    )


async def test_legacy_keeps_another_persons_workflow_hidden(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, _ = await _owners_workflow(db, monkeypatch)
    db.add(_grant(workflow.id, CALLER))
    await db.commit()
    assert (await client.get(f"/api/workflows/{workflow.id}")).status_code == 404
    assert (await client.get("/api/workflows")).json() == []


async def test_a_consumer_sees_and_runs_it_as_itself_but_cannot_edit(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, fake: FakeAgent
) -> None:
    workflow, agent = await _owners_workflow(db, monkeypatch)
    db.add(_grant(workflow.id, CALLER))
    await db.commit()
    me = f"user:{CALLER}"
    _enforce(
        monkeypatch,
        (me, "can_run", f"workflow:{workflow.id}"),
        (me, "can_run", f"agent:{agent.id}"),
        *_runs_own(workflow, agent),
    )
    listed = (await client.get("/api/workflows")).json()
    assert [item["id"] for item in listed] == [str(workflow.id)]
    detail = (await client.get(f"/api/workflows/{workflow.id}")).json()
    assert (detail["my_permissions"], detail["is_owner"]) == (["can_run"], False)
    edit = await client.put(
        f"/api/workflows/{workflow.id}/draft",
        json={"revision": detail["draft_revision"], "definition": detail["draft_definition"]},
    )
    assert edit.status_code == 403
    started = await client.post(
        f"/api/workflows/{workflow.id}/runs", json={"inputs": {"inquiry": "환불"}}
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    fake.answers.append({"category": "refund", "urgent": False})
    outcome = await _job(run_id)
    run = await runner.get_run(db, unit_context(CALLER), uuid.UUID(run_id))
    await db.refresh(run)
    assert outcome == {"outcome": "completed"}, (run.error_code, run.error_message)
    assert run.user_id == CALLER
    # The owner does not see the caller's run (S7).
    assert await runner.list_runs(db, unit_context(OWNER), workflow.id) == []


async def test_without_a_grant_or_can_run_it_stays_hidden(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, _ = await _owners_workflow(db, monkeypatch)
    db.add(_grant(workflow.id, CALLER))
    await db.commit()
    _enforce(monkeypatch)  # a grant row, but OpenFGA says no
    assert (await client.get(f"/api/workflows/{workflow.id}")).status_code == 404
    assert (await client.get("/api/workflows")).json() == []


async def test_an_editor_saves_the_draft(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, _ = await _owners_workflow(db, monkeypatch)
    db.add(_grant(workflow.id, CALLER, "editor"))
    await db.commit()
    me = f"user:{CALLER}"
    _enforce(
        monkeypatch,
        (me, "can_run", f"workflow:{workflow.id}"),
        (me, "can_edit", f"workflow:{workflow.id}"),
    )
    detail = (await client.get(f"/api/workflows/{workflow.id}")).json()
    assert detail["my_permissions"] == ["can_run", "can_edit"]
    saved = await client.put(
        f"/api/workflows/{workflow.id}/draft",
        json={
            "revision": detail["draft_revision"],
            "definition": detail["draft_definition"],
            "name": "편집자가 바꾼 이름",
        },
    )
    assert saved.status_code == 200, saved.text
    await db.refresh(workflow)
    assert (workflow.name, workflow.user_id) == ("편집자가 바꾼 이름", OWNER)  # owner unchanged
    archive = await client.post(f"/api/workflows/{workflow.id}/archive")
    assert archive.status_code == 403  # can_delete stays with the owner


async def test_the_share_routes_exist_for_workflows_without_transfer(client: AsyncClient) -> None:
    from app.main import app

    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/api/resources/workflow/{resource_id}/grants" in paths
    assert "/api/resources/workflow/{resource_id}/access" in paths
    assert "/api/resources/workflow/{resource_id}/transfer" not in paths


async def _waiting_request(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent, workflow: Workflow
) -> ApprovalRequest:
    from sqlalchemy import select

    run = await runner.start_run(db, unit_context(OWNER), workflow.id, {"inquiry": "급해요"})
    await db.commit()
    fake.answers.append({"category": "refund", "urgent": True})
    assert await _job(str(run.id)) == {"outcome": "waiting"}
    row = await db.scalar(select(ApprovalRequest).where(ApprovalRequest.workflow_run_id == run.id))
    assert row is not None
    return row


async def test_a_named_approver_needs_access_and_then_decides(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, fake: FakeAgent
) -> None:
    approver = {"kind": "user", "user_id": str(CALLER)}
    workflow, agent = await _owners_workflow(db, monkeypatch, approver)
    owner = unit_context(OWNER)
    # Legacy: nobody but the owner can run it, so the named approver is an issue.
    issues = await service.check_workflow(db, owner, workflow)
    assert [issue.code for issue in issues] == ["approver_no_access"]
    _enforce(
        monkeypatch,
        *_runs_own(workflow, agent),
        (f"user:{CALLER}", "can_run", f"workflow:{workflow.id}"),
    )
    assert await service.check_workflow(db, owner, workflow) == []
    row = await _waiting_request(client, db, fake, workflow)
    assert (row.decider_user_id, row.subject_user_id) == (CALLER, OWNER)
    assert "approver_fallback" not in row.actions[0]["args"]
    # The caller (the client) decides it from its inbox; the run continues as the owner's.
    decided = await client.post(
        f"/api/approvals/{row.id}/decision", json={"decisions": [{"type": "approve"}]}
    )
    assert decided.status_code == 200, decided.text
    assert await _job(str(row.workflow_run_id)) == {"outcome": "completed"}


async def test_an_approver_who_lost_access_hands_it_back_to_the_runner(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, fake: FakeAgent
) -> None:
    approver = {"kind": "user", "user_id": str(THIRD_USER_ID)}
    workflow, agent = await _owners_workflow(db, monkeypatch, approver)
    # The third member's access is gone by the time the run reaches the approval.
    _enforce(monkeypatch, *_runs_own(workflow, agent))
    row = await _waiting_request(client, db, fake, workflow)
    assert row.decider_user_id == OWNER
    assert row.actions[0]["args"]["approver_fallback"] == "approver_no_access"
