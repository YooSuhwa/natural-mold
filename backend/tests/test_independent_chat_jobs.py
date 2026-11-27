from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.service import PendingInterrupt, RunOrigin, record_pending
from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_org_context
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.models.memory import AgentMemorySettings, UserMemorySettings
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.schemas.independent_chat_job import IndependentJobOut, IndependentJobsOut
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_run_state import transition_run
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.test_agent_lifecycle_run_ownership import _seed_execution_tree


@dataclass(frozen=True)
class JobRequest:
    conversation_id: UUID
    run_id: UUID
    target_id: UUID
    request_id: UUID

    @property
    def listing(self) -> str:
        return f"/api/conversations/{self.conversation_id}/independent-jobs"

    @property
    def create_path(self) -> str:
        return f"/api/conversations/{self.conversation_id}/runs/{self.run_id}/independent-jobs"

    def payload(self) -> dict[str, str]:
        return {
            "target_id": str(self.target_id),
            "request_id": str(self.request_id),
            "instruction": "공개 자료에서 국내 업체 세 곳을 비교해 주세요.",
        }

    async def create(self, client: AsyncClient) -> IndependentJobOut:
        response = await client.post(self.create_path, json=self.payload())
        assert response.status_code == 201, response.text
        return IndependentJobOut.model_validate_json(response.content)


@pytest.fixture
def launch(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(settings, "independent_chat_jobs_enabled", True)
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    # Hold the scheduled wakeup after commit. The worker suite covers actual execution.
    wakeup = AsyncMock(return_value=None)
    monkeypatch.setattr("app.routers.independent_chat_jobs.launch_claimed_input", wakeup)
    return wakeup


@pytest.fixture
async def job_request(db: AsyncSession, launch: AsyncMock) -> JobRequest:
    root, target, conversation = await _seed_execution_tree(db)
    target.runtime_policy = {"version": 1}
    db.add(AgentMemorySettings(agent_id=target.id, memory_policy_override="off"))
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=root.id,
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="부모 조사",
        ),
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    await transition_run(db, run, "running", worker_instance_id="test-parent")
    await db.commit()
    return JobRequest(conversation.id, run.id, target.id, uuid4())


async def test_replaying_handoff_after_parent_finishes_keeps_one_durable_child(
    db: AsyncSession, client: AsyncClient, job_request: JobRequest, launch: AsyncMock
) -> None:
    first = await job_request.create(client)
    parent = await db.get(ConversationRun, job_request.run_id)
    assert parent is not None
    await transition_run(db, parent, "completed")
    await db.commit()

    replay = await client.post(job_request.create_path, json=job_request.payload())
    assert replay.status_code == 200, replay.text
    assert IndependentJobOut.model_validate_json(replay.content) == first
    conflict = await client.post(
        job_request.create_path, json={**job_request.payload(), "instruction": "바뀐 요청"}
    )
    assert conflict.status_code == 409
    assert await db.scalar(select(func.count()).select_from(IndependentChatJob)) == 1
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 2
    child = await db.get(Conversation, first.child_conversation_id)
    assert child is not None and child.source == "independent_job"
    assert child.user_id == TEST_USER_ID and child.agent_id == job_request.target_id
    assert child.id != job_request.conversation_id
    run = await db.get(ConversationRun, first.child_run_id)
    assert run is not None and run.status == "queued" and run.is_active
    assert run.runtime_policy_hash == child.runtime_policy_hash
    queued = (await db.scalars(select(ConversationRunInput))).one()
    assert queued.run_id == run.id and queued.conversation_id == child.id
    assert queued.status == "claimed"
    assert queued.input_payload["messages"] == [
        {
            "role": "user",
            "content": job_request.payload()["instruction"],
            "id": str(job_request.request_id),
        }
    ]
    launch.assert_awaited_once_with(queued.id)

    for conversation_id, is_child in [(child.id, True), (job_request.conversation_id, False)]:
        response = await client.get(f"/api/conversations/{conversation_id}/independent-jobs")
        assert response.status_code == 200
        data = IndependentJobsOut.model_validate_json(response.content)
        assert data.parent_job == (first if is_child else None)
        assert data.jobs == ([] if is_child else [first])

    await transition_run(db, run, "running", worker_instance_id="test-child")
    await transition_run(db, run, "completed")
    await db.commit()
    response = await client.get(job_request.listing)
    assert IndependentJobsOut.model_validate_json(response.content).jobs[0].status == "completed"


async def test_closed_parent_can_start_independent_work_but_child_cannot_redelegate(
    db: AsyncSession, client: AsyncClient, job_request: JobRequest
) -> None:
    parent = await db.get(ConversationRun, job_request.run_id)
    assert parent is not None
    await transition_run(db, parent, "completed")
    await db.commit()
    target_list = await client.get(job_request.listing, params={"run_id": str(parent.id)})
    assert target_list.status_code == 200, target_list.text
    assert [t.id for t in IndependentJobsOut.model_validate_json(target_list.content).targets] == [
        job_request.target_id
    ]
    job = await job_request.create(client)
    nested = await client.post(
        f"/api/conversations/{job.child_conversation_id}/runs/{job.child_run_id}/independent-jobs",
        json={**job_request.payload(), "request_id": str(uuid4())},
    )
    assert nested.status_code == 409
    assert await db.scalar(select(func.count()).select_from(IndependentChatJob)) == 1


@pytest.mark.parametrize("child_interrupted", [False, True], ids=["active", "awaiting-approval"])
async def test_child_cancel_is_idempotent_and_never_stops_parent(
    db: AsyncSession, client: AsyncClient, job_request: JobRequest, child_interrupted: bool
) -> None:
    job = await job_request.create(client)
    child_run = await db.get(ConversationRun, job.child_run_id)
    assert child_run is not None
    await transition_run(db, child_run, "running", worker_instance_id="test-child")
    if child_interrupted:
        await transition_run(db, child_run, "interrupted", interrupt_id="approval-fixture")
        (approval,) = await record_pending(
            db,
            child_run,
            [
                PendingInterrupt(
                    "approval-fixture",
                    {
                        "action_requests": [{"name": "read_source", "args": {"source": "public"}}],
                        "review_configs": [
                            {
                                "action_name": "read_source",
                                "allowed_decisions": ["approve", "reject"],
                            }
                        ],
                    },
                )
            ],
            origin=RunOrigin(),
        )
    await db.commit()
    for _ in range(2):
        response = await client.post(f"{job_request.listing}/{job.id}/cancel")
        assert response.status_code == 200, response.text
        status = IndependentJobOut.model_validate_json(response.content).status
        assert status == ("canceled" if child_interrupted else "canceling")
    if child_interrupted:
        await db.refresh(approval)
        assert approval.status == "canceled" and approval.decision_no == 1
    row = await db.get(IndependentChatJob, job.id, populate_existing=True)
    assert row is not None and row.cancel_requested_at is not None
    parent = await db.get(ConversationRun, job_request.run_id, populate_existing=True)
    assert parent is not None and parent.status == "running" and parent.cancel_requested_at is None


@pytest.mark.parametrize("cascade", [False, True], ids=["parent-only", "parent-and-jobs"])
async def test_parent_stop_propagates_only_when_selected(
    db: AsyncSession, client: AsyncClient, job_request: JobRequest, cascade: bool
) -> None:
    job = await job_request.create(client)
    response = await client.post(
        f"/api/conversations/{job_request.conversation_id}/runs/{job_request.run_id}/cancel",
        params={"cancel_independent_jobs": "true"} if cascade else {},
    )
    assert response.status_code == 200, response.text
    job_row = await db.get(IndependentChatJob, job.id, populate_existing=True)
    assert job_row is not None
    assert (job_row.cancel_requested_at is not None) == cascade
    run = await db.get(ConversationRun, job.child_run_id, populate_existing=True)
    assert run is not None and run.status == ("canceling" if cascade else "queued")


@pytest.mark.parametrize(
    "flag", ["independent_chat_jobs_enabled", "chat_work_notifications_enabled"]
)
async def test_disable_keeps_previous_jobs_accessible_and_stoppable(
    db: AsyncSession,
    client: AsyncClient,
    job_request: JobRequest,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    job = await job_request.create(client)
    monkeypatch.setattr(settings, flag, False)
    response = await client.get(job_request.listing, params={"run_id": str(job.parent_run_id)})
    assert response.status_code == 200
    data = IndependentJobsOut.model_validate_json(response.content)
    assert not data.enabled and data.targets == [] and data.jobs == [job]
    replay = await client.post(job_request.create_path, json=job_request.payload())
    assert replay.status_code == 200
    assert IndependentJobOut.model_validate_json(replay.content) == job
    new = await client.post(
        job_request.create_path, json={**job_request.payload(), "request_id": str(uuid4())}
    )
    assert 400 <= new.status_code < 500
    stopped = await client.post(f"{job_request.listing}/{job.id}/cancel")
    assert stopped.status_code == 200
    assert await db.scalar(select(func.count()).select_from(IndependentChatJob)) == 1


@pytest.mark.parametrize("same_user", [False, True], ids=["other-user", "other-company"])
async def test_copied_parent_and_child_links_hide_private_jobs(
    client: AsyncClient,
    db: AsyncSession,
    test_app: FastAPI,
    job_request: JobRequest,
    monkeypatch: pytest.MonkeyPatch,
    same_user: bool,
) -> None:
    job = await job_request.create(client)
    if same_user:
        tenant_id, org_id = uuid4(), uuid4()
        db.add(Tenant(id=tenant_id, name="B", slug="b", secrets_namespace=f"tenants/{tenant_id}"))
        await db.flush()
        db.add(Organization(id=org_id, tenant_id=tenant_id, name="B", slug="b"))
        await db.flush()
        db.add(TenantMember(tenant_id=tenant_id, user_id=TEST_USER_ID))
        db.add(OrganizationMember(org_id=org_id, tenant_id=tenant_id, user_id=TEST_USER_ID))
        await db.commit()
        monkeypatch.setitem(
            test_app.dependency_overrides,
            get_org_context,
            lambda: OrgContext(tenant_id=tenant_id, org_id=org_id, user_id=TEST_USER_ID),
        )
    else:
        other = CurrentUser(id=uuid4(), email="other-f05@example.test", name="Other")
        monkeypatch.setitem(test_app.dependency_overrides, get_current_user, lambda: other)
    for conversation_id in (job.parent_conversation_id, job.child_conversation_id, uuid4()):
        response = await client.get(f"/api/conversations/{conversation_id}/independent-jobs")
        assert response.status_code == 404
    for route, payload in [
        (job_request.create_path, job_request.payload()),
        (f"{job_request.listing}/{job.id}/cancel", {}),
    ]:
        response = await client.post(route, json=payload)
        assert response.status_code == 404
    row = await db.get(IndependentChatJob, job.id, populate_existing=True)
    assert row is not None and row.cancel_requested_at is None


async def test_approval_resume_updates_result_link_without_creating_another_job(
    db: AsyncSession,
    client: AsyncClient,
    job_request: JobRequest,
) -> None:
    job = await job_request.create(client)
    initial = await db.get(ConversationRun, job.child_run_id)
    assert initial is not None
    await transition_run(db, initial, "running", worker_instance_id="child-before-approval")
    await transition_run(db, initial, "interrupted", interrupt_id="research-approval")
    await db.commit()
    pending = await client.get(job_request.listing)
    assert (
        IndependentJobsOut.model_validate_json(pending.content).jobs[0].status
        == "waiting_for_approval"
    )

    resumed = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=job.child_conversation_id,
            root_agent_id=job.child_agent_id,
            context=unit_context(TEST_USER_ID),
            source="chat",
            run_source="resume",
            parent_run_id=initial.id,
            interrupt_id="research-approval",
            input_preview="approved",
        ),
    )
    new_run = await db.get(ConversationRun, resumed.run_id)
    assert new_run is not None
    await transition_run(db, new_run, "running", worker_instance_id="child-after-approval")
    await transition_run(db, new_run, "completed")
    await db.commit()
    response = await client.get(job_request.listing)
    (finished,) = IndependentJobsOut.model_validate_json(response.content).jobs
    assert finished.id == job.id and finished.status == "completed"
    assert finished.child_run_id == resumed.run_id and finished.child_run_id != initial.id


async def test_already_completed_parent_stop_cannot_cancel_its_live_child(
    db: AsyncSession,
    client: AsyncClient,
    job_request: JobRequest,
) -> None:
    job = await job_request.create(client)
    parent = await db.get(ConversationRun, job.parent_run_id)
    assert parent is not None
    await transition_run(db, parent, "completed")
    await db.commit()
    response = await client.post(
        f"/api/conversations/{job.parent_conversation_id}/runs/{job.parent_run_id}/cancel",
        params={"cancel_independent_jobs": "true"},
    )
    assert response.status_code == 200
    child = await db.get(ConversationRun, job.child_run_id, populate_existing=True)
    row = await db.get(IndependentChatJob, job.id, populate_existing=True)
    assert child is not None and child.status == "queued"
    assert row is not None and row.cancel_requested_at is None


async def test_independent_mutations_require_csrf_with_browser_cookie(
    raw_client: AsyncClient,
) -> None:
    from tests.conftest import register_session
    from tests.test_multiuser_isolation import _make_agent, _seed_default_model

    actor = await register_session(raw_client, email="f05-csrf@example.com", name="Browser")
    agent_id = await _make_agent(raw_client, actor, await _seed_default_model())
    conversation = await raw_client.post(
        f"/api/agents/{agent_id}/conversations", json={}, headers=actor.headers()
    )
    assert conversation.status_code == 201
    parent_id = UUID(conversation.json()["id"])
    fixture = JobRequest(parent_id, uuid4(), uuid4(), uuid4())
    assert (await raw_client.post(fixture.create_path, json=fixture.payload())).status_code == 403
    assert (await raw_client.post(f"{fixture.listing}/{uuid4()}/cancel")).status_code == 403


@pytest.mark.asyncio
async def test_inherited_writable_memory_hides_target_and_refuses_explicit_handoff(
    client: AsyncClient, db: AsyncSession, job_request: JobRequest, launch: AsyncMock
) -> None:
    override = await db.get(AgentMemorySettings, job_request.target_id)
    assert override is not None
    await db.delete(override)
    await db.commit()
    response = await client.get(job_request.listing, params={"run_id": str(job_request.run_id)})
    assert response.status_code == 200
    assert IndependentJobsOut.model_validate_json(response.content).targets == []
    rejected = await client.post(job_request.create_path, json=job_request.payload())
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "independent_job_unsupported_agent"
    assert await db.scalar(select(func.count()).select_from(IndependentChatJob)) == 0
    assert await db.get(UserMemorySettings, TEST_USER_ID) is None
    launch.assert_not_awaited()


async def test_independent_room_rejects_goals_and_root_steer(
    db: AsyncSession, client: AsyncClient, job_request: JobRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.conversation_goal import ConversationGoal
    from app.models.conversation_run_steer import ConversationRunSteer

    context = unit_context(TEST_USER_ID)
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({context.org_id}))
    monkeypatch.setattr(settings, "chat_steer_enabled", True)
    job = await job_request.create(client)
    base = f"/api/conversations/{job.child_conversation_id}"
    steer = f"{base}/runs/{job.child_run_id}/steers"
    goal = {
        "expected_revision": 0,
        "title": "가격 비교",
        "criteria": [{"kind": "nonempty_file", "logical_path": "표.csv"}],
    }
    assert (await client.get(f"{base}/goal")).json() == {"enabled": False, "goal": None}
    assert (await client.put(f"{base}/goal", json=goal)).status_code == 409
    response = await client.post(steer, json={"client_request_id": str(uuid4()), "text": "새 조사"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STEER_RUN_INACTIVE"
    assert (await client.get(steer)).json() == []
    assert await db.scalar(select(func.count()).select_from(ConversationRunSteer)) == 0
    assert await db.scalar(select(func.count()).select_from(ConversationGoal)) == 0
    assert await db.scalar(select(func.count()).select_from(IndependentChatJob)) == 1

    run = await db.get(ConversationRun, job.child_run_id)
    assert run is not None and run.run_source == "CHAT" and run.source == "start"
    await transition_run(db, run, "running", worker_instance_id="independent")
    await transition_run(db, run, "completed")
    await db.commit()
    from app.services.conversation_goal_worker import reconcile_goal_run

    await reconcile_goal_run(db, run.id, job.child_conversation_id)
    await db.commit()
    inputs = (await db.scalars(select(ConversationRunInput))).all()
    assert len(inputs) == 1 and inputs[0].run_id == run.id
    assert inputs[0].source != "goal_followup"
