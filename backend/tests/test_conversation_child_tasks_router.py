"""Exercise child commands through ordinary chat acceptance and scoped HTTP routes."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import get_org_context
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization, OrganizationMember
from app.services import agent_execution_owner
from app.services.agent_execution_acceptance import OwnedExecutionRequest
from app.services.child_task_errors import ChildTaskStopped
from app.services.child_tasks_runtime import begin_child_task, next_child_instructions
from app.services.conversation_run_service import RunSource
from app.services.conversation_run_state import transition_run
from tests.authz.unit_scope_fixture import unit_context
from tests.test_agent_lifecycle_run_ownership import _seed_execution_tree
from tests.test_independent_chat_jobs import JobRequest
from tests.test_independent_chat_jobs import job_request as job_request
from tests.test_independent_chat_jobs import launch as launch

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class Delegation:
    conversation_id: UUID
    run_id: UUID
    child_id: UUID
    sibling_id: UUID
    child_agent_id: UUID
    context: OrgContext

    @property
    def base(self) -> str:
        return f"/api/conversations/{self.conversation_id}/child-tasks"


@pytest.fixture
async def delegation(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Delegation:
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    root, researcher, conversation = await _seed_execution_tree(db)
    context = unit_context(root.user_id)
    sibling = Agent(
        user_id=root.user_id,
        name="Writer",
        system_prompt="Write.",
        model_id=root.model_id,
        org_id=root.org_id,
        tenant_id=root.tenant_id,
    )
    db.add(sibling)
    await db.flush()
    db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=sibling.id, position=1))
    await db.commit()
    accepted = await agent_execution_owner.accept_owned_execution(
        db, OwnedExecutionRequest(conversation.id, root.id, context, "chat", "Compare the cases")
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    run.status = "running"
    await db.commit()

    async def register(target: Agent, call: str) -> UUID:
        child_id = await begin_child_task(
            db,
            context=context,
            conversation_id=conversation.id,
            run_id=run.id,
            child_agent_id=target.id,
            tool_call_id=call,
            role=target.name,
            brief=f"{target.name} 사례 검토",
            checkpoint_ns=f"tools:{call}|child",
        )
        await db.commit()
        assert child_id is not None
        return child_id

    return Delegation(
        conversation.id,
        run.id,
        await register(researcher, "call-research"),
        await register(sibling, "call-write"),
        researcher.id,
        context,
    )


async def test_targeted_instruction_and_cancellation_are_idempotent(
    client: AsyncClient,
    delegation: Delegation,
) -> None:
    target = f"{delegation.base}/{delegation.child_id}"
    sibling = f"{delegation.base}/{delegation.sibling_id}"
    payload = {"client_request_id": "receipt-1", "text": "공공기관 사례만 남겨 주세요."}
    sent = await client.post(f"{target}/instructions", json=payload)
    assert sent.status_code == 200, sent.text
    retry = await client.post(f"{target}/instructions", json=payload)
    assert retry.status_code == 200
    assert sent.json()["instructions"] == retry.json()["instructions"]
    assert [(item["text"], item["status"]) for item in retry.json()["instructions"]] == [
        (payload["text"], "pending")
    ]
    conflict = await client.post(f"{target}/instructions", json={**payload, "text": "다른 지시"})
    assert conflict.status_code == 409
    timed_out = await client.get(f"{sibling}/wait", params={"timeout_seconds": 0.01})
    assert timed_out.status_code == 200
    assert timed_out.json()["status"] == "running"
    assert timed_out.json()["instructions"] == []
    assert timed_out.json()["checkpointed"] is False

    for _ in range(2):
        cancelled = await client.post(f"{target}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert [r["status"] for r in cancelled.json()["instructions"]] == ["cancelled"]
    late = await client.post(
        f"{target}/instructions", json={**payload, "client_request_id": "late"}
    )
    assert late.status_code == 409
    # Idempotency is per target: another child may use the same request ID.
    sent_sibling = await client.post(f"{sibling}/instructions", json=payload)
    assert sent_sibling.status_code == 200
    assert sent_sibling.json()["instructions"][0]["id"] != sent.json()["instructions"][0]["id"]
    listed = await client.get(delegation.base)
    assert listed.status_code == 200
    assert {row["id"]: row["status"] for row in listed.json()["items"]} == {
        str(delegation.child_id): "cancelled",
        str(delegation.sibling_id): "running",
    }


async def test_approval_pauses_delivery_and_does_not_resume_parent(
    db: AsyncSession,
    client: AsyncClient,
    delegation: Delegation,
) -> None:
    await db.execute(
        update(ConversationRun)
        .where(ConversationRun.id == delegation.run_id)
        .values(status="interrupted", interrupt_id="review", is_active=False)
    )
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.id == delegation.child_id)
        .values(status="awaiting_approval", checkpoint_id="checkpoint-before-review")
    )
    await db.commit()
    url = f"{delegation.base}/{delegation.child_id}"
    response = await client.post(
        f"{url}/instructions", json={"client_request_id": "waiting", "text": "승인 후 확인"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_approval"
    assert response.json()["checkpointed"] is True
    assert response.json()["instructions"][0]["status"] == "pending"
    waited = await client.get(f"{url}/wait", params={"timeout_seconds": 10})
    assert waited.status_code == 200
    assert waited.json() == response.json()
    parent = await db.get(ConversationRun, delegation.run_id, populate_existing=True)
    assert parent is not None and parent.status == "interrupted" and parent.interrupt_id == "review"


async def test_revoked_child_is_hidden_while_sibling_remains_authorized(
    db: AsyncSession,
    client: AsyncClient,
    delegation: Delegation,
) -> None:
    base = delegation.base
    url = f"{base}/{delegation.child_id}"
    payload = {"client_request_id": "before-revoke", "text": "허용된 지시"}
    assert (await client.post(f"{url}/instructions", json=payload)).status_code == 200
    # Position the soon-to-be-revoked candidate before the other active child.
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.id == delegation.child_id)
        .values(created_at=datetime(2099, 1, 1))
    )
    await db.execute(
        delete(AgentSubAgentLink).where(AgentSubAgentLink.sub_agent_id == delegation.child_agent_id)
    )
    await db.commit()
    unknown = (await client.get(f"{base}/{uuid4()}/wait", params={"timeout_seconds": 0})).json()
    for denied in [
        await client.post(f"{url}/instructions", json=payload),
        await client.post(f"{url}/cancel"),
        await client.get(f"{url}/wait", params={"timeout_seconds": 0}),
    ]:
        assert denied.status_code == 404 and denied.json() == unknown
    page = await client.get(base, params={"limit": 1})
    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == [str(delegation.sibling_id)]
    assert page.json()["next_offset"] is None
    after = await client.get(base, params={"limit": 1, "offset": 1})
    assert after.status_code == 200
    assert after.json()["items"] == [] and after.json()["next_offset"] is None


async def test_disabled_feature_preserves_replay_wait_and_cancel(
    client: AsyncClient,
    delegation: Delegation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = f"{delegation.base}/{delegation.child_id}"
    payload = {"client_request_id": "previous", "text": "이전 지시 확인"}
    sent = await client.post(f"{target}/instructions", json=payload)
    assert sent.status_code == 200
    monkeypatch.setattr(settings, "child_tasks_enabled", False)
    listed = await client.get(delegation.base)
    assert listed.status_code == 200 and listed.json()["enabled"] is False
    assert {row["id"] for row in listed.json()["items"]} == {
        str(delegation.child_id),
        str(delegation.sibling_id),
    }
    waited = await client.get(f"{target}/wait", params={"timeout_seconds": 0})
    assert waited.status_code == 200 and waited.json()["status"] == "running"
    replay = await client.post(f"{target}/instructions", json=payload)
    assert (
        replay.status_code == 200 and replay.json()["instructions"] == sent.json()["instructions"]
    )
    denied = await client.post(
        f"{target}/instructions", json={**payload, "client_request_id": "next"}
    )
    assert denied.status_code == 409
    stopped = await client.post(f"{target}/cancel")
    assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
    assert [item["status"] for item in stopped.json()["instructions"]] == ["cancelled"]


@pytest.mark.parametrize("resume_status", ["running", "completed"])
async def test_consumed_interrupt_cannot_accept_more_child_instructions(
    db: AsyncSession,
    client: AsyncClient,
    delegation: Delegation,
    resume_status: str,
) -> None:
    run = await db.get(ConversationRun, delegation.run_id)
    assert run is not None and run.agent_id is not None
    run.status, run.is_active, run.interrupt_id = "interrupted", False, "approval"
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.id == delegation.child_id)
        .values(status="awaiting_approval")
    )
    await db.commit()
    resumed = await agent_execution_owner.accept_owned_execution(
        db,
        OwnedExecutionRequest(
            delegation.conversation_id,
            run.agent_id,
            delegation.context,
            "chat",
            None,
            run_source="resume",
            parent_run_id=run.id,
            interrupt_id="approval",
        ),
    )
    continuation = await db.get(ConversationRun, resumed.run_id)
    assert continuation is not None
    continuation.status, continuation.is_active = resume_status, resume_status == "running"
    await db.commit()
    target = f"{delegation.base}/{delegation.child_id}"
    denied = await client.post(
        f"{target}/instructions", json={"client_request_id": "late", "text": "오래된 실행"}
    )
    assert denied.status_code == 409
    historical = await client.get(f"{target}/wait", params={"timeout_seconds": 0})
    assert historical.status_code == 200 and historical.json()["instructions"] == []


async def test_child_page_prioritizes_actionable_work_and_returns_each_item_once(
    db: AsyncSession,
    client: AsyncClient,
    delegation: Delegation,
) -> None:
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.id == delegation.child_id)
        .values(checkpoint_id="saved-v1")
    )
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.id == delegation.sibling_id)
        .values(status="completed", result="검토 완료")
    )
    await db.commit()
    pages = []
    for offset in (0, 1, 2):
        page = await client.get(delegation.base, params={"limit": 1, "offset": offset})
        assert page.status_code == 200
        pages.append(page.json())
    assert [[item["id"] for item in page["items"]] for page in pages] == [
        [str(delegation.child_id)],
        [str(delegation.sibling_id)],
        [],
    ]
    assert [page["next_offset"] for page in pages] == [1, None, None]
    assert all(page["enabled"] for page in pages)
    item = pages[0]["items"][0]
    assert item["child_agent_id"] == str(delegation.child_agent_id)
    assert item["checkpointed"] is True and item["brief"] == "Child 사례 검토"


async def test_legacy_detail_access_uses_immutable_root_in_this_conversation(
    db: AsyncSession,
    client: AsyncClient,
    delegation: Delegation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = delegation.context
    captured_root = (
        await db.scalars(
            select(ConversationRunAgent).where(
                ConversationRunAgent.run_id == delegation.run_id,
                ConversationRunAgent.role == "root",
            )
        )
    ).one()
    assert captured_root.identity_mode_at_accept is not None and captured_root.agent_id is not None

    # Simulate retained history from before child controls captured the root identity.
    history = Conversation(
        agent_id=captured_root.agent_id,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        agent_name_snapshot="Root",
        last_activity_at=datetime.now(UTC),
    )
    db.add(history)
    await db.flush()
    historical_run = ConversationRun(
        conversation_id=history.id,
        agent_id=captured_root.agent_id,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        source="chat",
        status="completed",
    )
    db.add(historical_run)
    await db.flush()
    db.add(
        ConversationRunAgent(
            run_id=historical_run.id,
            agent_id=captured_root.agent_id,
            agent_id_snapshot=captured_root.agent_id_snapshot,
            role="root",
            identity_mode_at_accept=None,
            path=[],
        )
    )
    # Removing current child material must not reopen an obsolete detail route.
    await db.execute(
        delete(ConversationChildTask).where(ConversationChildTask.run_id == delegation.run_id)
    )
    await db.execute(
        delete(AgentSubAgentLink).where(AgentSubAgentLink.parent_agent_id == captured_root.agent_id)
    )
    await db.commit()
    history_url = f"/api/conversations/{history.id}/child-tasks"
    enabled_history = await client.get(history_url)
    assert enabled_history.status_code == 200
    assert enabled_history.json()["allow_legacy_details"] is False
    monkeypatch.setattr(settings, "child_tasks_enabled", False)
    new_history = await client.get(delegation.base)
    old_history = await client.get(history_url)
    assert new_history.status_code == old_history.status_code == 200
    assert new_history.json()["items"] == old_history.json()["items"] == []
    assert new_history.json()["allow_legacy_details"] is False
    assert old_history.json()["allow_legacy_details"] is True


async def test_task_id_in_another_org_is_indistinguishable_from_unknown(
    db: AsyncSession,
    client: AsyncClient,
    delegation: Delegation,
    test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same user is a member of both orgs; ownership alone must not expose the first org's child.
    ctx = delegation.context
    other = Organization(tenant_id=ctx.tenant_id, name="Second", slug="second")
    db.add(other)
    await db.flush()
    db.add(OrganizationMember(tenant_id=ctx.tenant_id, org_id=other.id, user_id=ctx.user_id))
    first_agent = await db.get(Agent, delegation.child_agent_id)
    assert first_agent is not None
    agent = Agent(
        model_id=first_agent.model_id,
        name="Second root",
        system_prompt="Assist.",
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        org_id=other.id,
    )
    db.add(agent)
    await db.flush()
    own = Conversation(
        agent_id=agent.id,
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        org_id=other.id,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(own)
    await db.commit()
    other_context = OrgContext(tenant_id=ctx.tenant_id, org_id=other.id, user_id=ctx.user_id)
    monkeypatch.setitem(test_app.dependency_overrides, get_org_context, lambda: other_context)
    base = f"/api/conversations/{own.id}/child-tasks"
    assert (await client.get(base)).json() == {
        "items": [],
        "enabled": True,
        "next_offset": None,
        "allow_legacy_details": False,
    }
    known = await client.post(f"{base}/{delegation.child_id}/cancel")
    unknown = await client.post(f"{base}/{uuid4()}/cancel")
    assert known.status_code == unknown.status_code == 404
    assert known.json() == unknown.json()


async def test_private_child_history_requires_an_issued_independent_job(
    db: AsyncSession, client: AsyncClient, delegation: Delegation
) -> None:
    # The original child already has valid chat participant pins. Only its job proof is missing.
    room = await db.get(Conversation, delegation.conversation_id)
    assert room is not None
    room.source = INDEPENDENT_JOB_SOURCE
    await db.commit()
    target = f"{delegation.base}/{delegation.child_id}"
    request = {"client_request_id": "unchanged", "text": "이전 조사 수정"}

    for response in (
        await client.get(delegation.base),
        await client.get(f"{target}/wait", params={"timeout_seconds": 0}),
        await client.post(f"{target}/instructions", json=request),
        await client.post(f"{target}/cancel"),
    ):
        assert response.status_code == 404, response.text

    with pytest.raises(ChildTaskStopped):
        await next_child_instructions(
            db, context=delegation.context, child_id=delegation.child_id, run_id=delegation.run_id
        )
    with pytest.raises(ChildTaskStopped):
        await begin_child_task(
            db,
            context=delegation.context,
            conversation_id=delegation.conversation_id,
            run_id=delegation.run_id,
            child_agent_id=delegation.child_agent_id,
            tool_call_id="nested",
            role="researcher",
            checkpoint_ns="tools:nested|child",
        )


async def test_independent_room_preserves_child_history_and_rejects_nested_commands(
    db: AsyncSession,
    client: AsyncClient,
    job_request: JobRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    target = await db.get(Agent, job_request.target_id)
    assert target is not None
    context = unit_context(target.user_id)
    grandchild = Agent(
        name="검토자",
        system_prompt="Review the report.",
        model_id=target.model_id,
        user_id=target.user_id,
        tenant_id=target.tenant_id,
        org_id=target.org_id,
    )
    other_room = Conversation(
        agent_id=target.id,
        agent_name_snapshot=target.name,
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        last_activity_at=datetime.now(UTC),
    )
    db.add_all([grandchild, other_room])
    await db.flush()
    other_room_id = other_room.id
    grandchild_id = grandchild.id
    db.add(AgentSubAgentLink(parent_agent_id=target.id, sub_agent_id=grandchild_id, position=0))
    await db.commit()
    job = await job_request.create(client)
    initial = await db.get(ConversationRun, job.child_run_id)
    assert initial is not None
    await transition_run(db, initial, "running", worker_instance_id="job-before-approval")
    await transition_run(db, initial, "interrupted", interrupt_id="approve")
    await db.commit()

    async def admit(
        room_id: UUID, source: RunSource, parent_id: UUID | None = None
    ) -> ConversationRun:
        accepted = await agent_execution_owner.accept_owned_execution(
            db,
            OwnedExecutionRequest(
                room_id,
                job.child_agent_id,
                context,
                "chat",
                "검토",
                run_source=source,
                parent_run_id=parent_id,
                interrupt_id="approve" if parent_id else None,
            ),
        )
        run = await db.get(ConversationRun, accepted.run_id)
        assert run is not None
        await transition_run(db, run, "running", worker_instance_id="child-run")
        return run

    # Preserve real participant pins, then simulate a historical run attached to the wrong room.
    unrelated = await admit(other_room_id, "start")
    unrelated_id = unrelated.id
    await transition_run(db, unrelated, "completed")
    unrelated.conversation_id = job.child_conversation_id
    await db.commit()
    resume = await admit(job.child_conversation_id, "resume", job.child_run_id)

    def historical_binding(call: str, origin: UUID, current: UUID) -> ConversationChildTask:
        binding = ConversationChildTask(
            tool_call_id=call,
            role="검토자",
            brief=call,
            child_agent_id=grandchild_id,
            run_id=origin,
            current_run_id=current,
            checkpoint_ns=f"tools:{call}|child",
        )
        db.add(binding)
        return binding

    # Insert already persisted historical bindings; new nested work is forbidden in this room.
    historical = historical_binding("owned-review", job.child_run_id, resume.id)
    await db.flush()
    mixed_origin = historical_binding("unrelated-origin", unrelated_id, resume.id)
    mixed_current = historical_binding("unrelated-current", job.child_run_id, unrelated_id)
    await db.flush()
    owned_id = historical.id
    hidden_ids = [mixed_origin.id, mixed_current.id]
    await db.commit()

    base = f"/api/conversations/{job.child_conversation_id}/child-tasks"
    page = await client.get(base, params={"limit": 1})
    assert page.status_code == 200, page.text
    assert [item["id"] for item in page.json()["items"]] == [str(owned_id)]
    assert page.json()["next_offset"] is None
    assert (await client.get(base, params={"offset": 1})).json()["items"] == []
    request = {"client_request_id": "unchanged", "text": "이전 조사 수정"}
    unknown = await client.get(f"{base}/{uuid4()}/wait", params={"timeout_seconds": 0})
    assert unknown.status_code == 404
    for child_id in hidden_ids:
        for response in (
            await client.get(f"{base}/{child_id}/wait", params={"timeout_seconds": 0}),
            await client.post(f"{base}/{child_id}/instructions", json=request),
            await client.post(f"{base}/{child_id}/cancel"),
        ):
            assert response.status_code == 404, response.text
            assert response.json() == unknown.json()
    assert (await client.post(f"{base}/{owned_id}/instructions", json=request)).status_code == 409
    assert (await client.post(f"{base}/{owned_id}/cancel")).status_code == 409
    history = await client.get(f"{base}/{owned_id}/wait", params={"timeout_seconds": 0})
    assert history.status_code == 200, history.text
    assert history.json()["brief"] == "owned-review"
    assert history.json()["status"] == "running"
    assert history.json()["instructions"] == []

    monkeypatch.setattr(settings, "child_tasks_enabled", False)
    disabled = await client.get(base)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["allow_legacy_details"] is False
    assert [item["id"] for item in disabled.json()["items"]] == [str(owned_id)]
