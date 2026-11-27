"""An independent handoff follows an admitted child across its parent's approval resume."""

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.child_tasks.persistence import begin_child_task, set_child_task_status
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.models.memory import AgentMemorySettings
from app.schemas.independent_chat_job import IndependentJobOut
from app.services import chat_service
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_run_state import transition_run
from tests import test_conversation_child_tasks_router as child_routes
from tests import test_independent_chat_jobs as independent_routes
from tests.test_conversation_child_tasks_router import Delegation
from tests.test_independent_chat_jobs import JobRequest

# The two real child tasks and their participant records are created by F04's ordinary
# conversation fixture. The F05 fixture only holds the post-commit queue wakeup.
delegation = child_routes.delegation
launch = independent_routes.launch


@pytest.fixture
async def handoff(db: AsyncSession, delegation: Delegation, launch: AsyncMock) -> JobRequest:
    agent = await db.get(Agent, delegation.child_agent_id)
    assert agent is not None
    agent.runtime_policy = {"version": 1}
    db.add(AgentMemorySettings(agent_id=agent.id, memory_policy_override="off"))
    await db.commit()
    return JobRequest(delegation.conversation_id, delegation.run_id, agent.id, uuid4())


def card(request: JobRequest, source: UUID) -> dict[str, str]:
    return {**request.payload(), "source_child_task_id": str(source)}


async def resume_parent(db: AsyncSession, pair: Delegation) -> UUID:
    parent = await db.get(ConversationRun, pair.run_id)
    assert parent and parent.agent_id
    await set_child_task_status(
        db,
        context=pair.context,
        child_id=pair.child_id,
        run_id=parent.id,
        status="awaiting_approval",
    )
    await db.commit()
    await transition_run(db, parent, "interrupted", interrupt_id="review-research-plan")
    await db.commit()
    resumed = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            pair.conversation_id,
            parent.agent_id,
            pair.context,
            "chat",
            "continue",
            run_source="resume",
            parent_run_id=parent.id,
            interrupt_id="review-research-plan",
        ),
    )
    run = await db.get(ConversationRun, resumed.run_id)
    assert run is not None
    await transition_run(db, run, "running", worker_instance_id="resumed-parent")
    await db.commit()
    same_child = await begin_child_task(
        db,
        context=pair.context,
        conversation_id=pair.conversation_id,
        run_id=run.id,
        tool_call_id="call-research",
        child_agent_id=pair.child_agent_id,
        role="Child",
        checkpoint_ns="tools:call-research|child",
    )
    assert same_child == pair.child_id
    await db.commit()
    task = await db.get(ConversationChildTask, pair.child_id, populate_existing=True)
    assert task and (task.run_id, task.current_run_id) == (parent.id, resumed.run_id)
    return resumed.run_id


@pytest.mark.parametrize("start_before_resume", [True, False], ids=["replay", "new-handoff"])
async def test_origin_task_handoff_survives_parent_approval_resume(
    db: AsyncSession,
    client: AsyncClient,
    delegation: Delegation,
    handoff: JobRequest,
    launch: AsyncMock,
    start_before_resume: bool,
) -> None:
    first_id = None
    if start_before_resume:
        created = await client.post(handoff.create_path, json=card(handoff, delegation.child_id))
        assert created.status_code == 201, created.text
        first_id = IndependentJobOut.model_validate_json(created.content).id
    resumed = await resume_parent(db, delegation)
    retry = replace(handoff, request_id=uuid4())
    response = await client.post(handoff.create_path, json=card(retry, delegation.child_id))
    assert response.status_code == (200 if start_before_resume else 201), response.text
    job = IndependentJobOut.model_validate_json(response.content)
    assert (
        job.parent_run_id == delegation.run_id and job.child_agent_id == delegation.child_agent_id
    )
    if first_id is not None:
        assert job.id == first_id
    again = await client.post(handoff.create_path, json=card(handoff, delegation.child_id))
    assert (
        again.status_code == 200
        and IndependentJobOut.model_validate_json(again.content).id == job.id
    )

    via_resume = replace(handoff, run_id=resumed)
    wrong_route = await client.post(
        via_resume.create_path, json=card(via_resume, delegation.child_id)
    )
    assert wrong_route.status_code == 404
    assert await db.scalar(select(func.count()).select_from(ConversationRunInput)) == 1
    launch.assert_awaited_once()


async def test_split_idempotency_keys_do_not_alias_different_child_calls(
    client: AsyncClient,
    db: AsyncSession,
    handoff: JobRequest,
    delegation: Delegation,
    launch: AsyncMock,
) -> None:
    other_call = await begin_child_task(
        db,
        context=delegation.context,
        conversation_id=delegation.conversation_id,
        run_id=delegation.run_id,
        tool_call_id="second-report",
        child_agent_id=handoff.target_id,
        role="Child",
        checkpoint_ns="tools:second-report|child",
    )
    assert other_call is not None and other_call != delegation.child_id
    await db.commit()
    second_request = replace(handoff, request_id=uuid4())
    for request, source in [(handoff, delegation.child_id), (second_request, other_call)]:
        created = await client.post(request.create_path, json=card(request, source))
        assert created.status_code == 201, created.text
    # Each key individually names an accepted job but they point to different work.
    split = await client.post(handoff.create_path, json=card(handoff, other_call))
    assert split.status_code == 409
    conflict = await client.post(
        handoff.create_path,
        json={
            **card(replace(handoff, request_id=uuid4()), delegation.child_id),
            "instruction": "다른 조사",
        },
    )
    assert conflict.status_code == 409
    assert await db.scalar(select(func.count()).select_from(IndependentChatJob)) == 2
    assert launch.await_count == 2


@pytest.mark.parametrize("bad_task", ["sibling", "cancelled", "foreign-parent"])
async def test_wrong_or_cancelled_source_cannot_create_independent_work(
    client: AsyncClient,
    db: AsyncSession,
    handoff: JobRequest,
    delegation: Delegation,
    launch: AsyncMock,
    bad_task: str,
) -> None:
    source = delegation.child_id
    if bad_task == "sibling":
        source = delegation.sibling_id
    elif bad_task == "cancelled":
        await set_child_task_status(
            db,
            context=delegation.context,
            child_id=source,
            run_id=delegation.run_id,
            status="cancelled",
        )
        await db.commit()
    else:
        # Same owner, parent agent and child agent. Only the originating chat differs.
        parent = await db.get(ConversationRun, delegation.run_id)
        assert parent and parent.agent_id
        conversation = await chat_service.create_conversation(
            db, parent.agent_id, "Other chat", context=delegation.context
        )
        await db.commit()
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation.id, parent.agent_id, delegation.context, "chat", "second"
            ),
        )
        run = await db.get(ConversationRun, accepted.run_id)
        assert run is not None
        await transition_run(db, run, "running", worker_instance_id="second-chat")
        await db.commit()
        foreign = await begin_child_task(
            db,
            context=delegation.context,
            conversation_id=conversation.id,
            run_id=run.id,
            tool_call_id="call-research",
            child_agent_id=delegation.child_agent_id,
            role="Child",
            checkpoint_ns="tools:call-research|child",
        )
        assert foreign is not None
        await db.commit()
        source = foreign

    response = await client.post(handoff.create_path, json=card(handoff, source))
    assert response.status_code == 404
    assert await db.scalar(select(func.count()).select_from(IndependentChatJob)) == 0
    launch.assert_not_awaited()


@pytest.mark.parametrize(
    "broken", ["unrelated-run", "missing-child", "credential-identity", "child-mode", "root-mode"]
)
async def test_changed_resumed_parent_cannot_authorize_original_source_card(
    client: AsyncClient,
    db: AsyncSession,
    handoff: JobRequest,
    delegation: Delegation,
    launch: AsyncMock,
    broken: str,
) -> None:
    resumed_id = await resume_parent(db, delegation)
    run = await db.get(ConversationRun, resumed_id)
    assert run and run.agent_id
    current_child = (
        ConversationRunAgent.run_id == resumed_id,
        ConversationRunAgent.agent_id_snapshot == delegation.child_agent_id,
    )
    if broken == "unrelated-run":
        # This valid same-chat run has no resume relationship to the original.
        await transition_run(db, run, "completed")
        await db.commit()
        orphan = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                delegation.conversation_id, run.agent_id, delegation.context, "chat", "new turn"
            ),
        )
        ordinary = await db.get(ConversationRun, orphan.run_id)
        assert ordinary is not None
        await transition_run(db, ordinary, "running", worker_instance_id="later-turn")
        await db.execute(
            update(ConversationChildTask)
            .where(
                ConversationChildTask.id == delegation.child_id,
            )
            .values(current_run_id=orphan.run_id, status="running")
        )
    elif broken == "missing-child":
        await db.execute(delete(ConversationRunAgent).where(*current_child))
    elif broken == "credential-identity":
        await db.execute(
            update(ConversationRunAgent)
            .where(*current_child)
            .values(credential_subject_user_id=None)
        )
    elif broken == "child-mode":
        await db.execute(
            update(ConversationRunAgent).where(*current_child).values(identity_mode_at_accept=None)
        )
    else:
        await db.execute(
            update(ConversationRunAgent)
            .where(
                ConversationRunAgent.run_id == resumed_id,
                ConversationRunAgent.role == "root",
            )
            .values(identity_mode_at_accept=None)
        )
    await db.commit()
    response = await client.post(handoff.create_path, json=card(handoff, delegation.child_id))
    assert response.status_code == 404
    assert await db.scalar(select(func.count()).select_from(IndependentChatJob)) == 0
    launch.assert_not_awaited()
