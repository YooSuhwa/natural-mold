from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.models.token_usage import TokenUsage
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.test_independent_child_read_scope import _child_run

pytestmark = pytest.mark.asyncio

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


def queued_input(
    job: IndependentChatJob, run_id: UUID | None, label: str, position: int
) -> ConversationRunInput:
    return ConversationRunInput(
        conversation_id=job.child_conversation_id,
        user_id=job.user_id,
        agent_id=job.child_agent_id,
        client_request_id=label,
        source="chat",
        status="claimed" if run_id else "pending",
        position=position,
        input_payload={"content": label},
        run_id=run_id,
    )


async def test_history_limits_rows_to_managed_start_and_resumes(
    client: AsyncClient, db: AsyncSession, delegation: Delegation
) -> None:
    context, _, initial, job = delegation
    initial.status, initial.is_active = "interrupted", False
    resumed = _child_run(db, job, source="resume", parent_id=initial.id)
    other = _child_run(db, job, source="start", parent_id=None, status="running")
    await db.flush()
    other.is_active = True
    other.created_at = resumed.created_at + timedelta(minutes=1)
    other.input_preview = "detached-old-preview"
    db.add_all(
        [
            queued_input(job, initial.id, "accepted-start", 1),
            queued_input(job, resumed.id, "accepted-resume", 2),
            queued_input(job, other.id, "detached-old-input", 3),
            queued_input(job, None, "detached-old-queued", 4),
        ]
    )
    for run_id in [initial.id, resumed.id, other.id, None]:
        db.add(
            TokenUsage(
                run_id=run_id,
                tenant_id=context.tenant_id,
                org_id=context.org_id,
                caller_user_id=context.user_id,
                conversation_id=job.child_conversation_id,
                agent_id=job.child_agent_id,
                model_name="projection-test",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )
        )
    await db.commit()

    prefix = f"/api/conversations/{job.child_conversation_id}"
    history = await client.get(f"{prefix}/history")
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["latest_run"]["id"] == str(resumed.id)
    assert {item["id"] for item in body["runs"]} == {str(initial.id), str(resumed.id)}
    assert [item["client_request_id"] for item in body["inputs"]] == [
        "accepted-start",
        "accepted-resume",
    ]
    assert {item["run_id"] for item in body["usage"]} == {str(initial.id), str(resumed.id)}
    assert "detached-old" not in history.text
    queue = await client.get(f"{prefix}/run-inputs")
    assert queue.status_code == 200 and queue.json()["items"] == body["inputs"]

    messages = await client.get(f"{prefix}/messages")
    assert messages.status_code == 200, messages.text
    assert messages.json()["active_run"] is None
    assert messages.json()["latest_run"]["id"] == str(resumed.id)
    state = await client.get(f"{prefix}/langgraph/threads/{job.child_conversation_id}/state")
    assert state.status_code == 200, state.text
    assert str(resumed.id) in state.text
    assert str(other.id) not in state.text


async def test_unrelated_resume_cannot_hide_original_interrupted_run(
    client: AsyncClient, db: AsyncSession, delegation: Delegation
) -> None:
    _, _, initial, job = delegation
    initial.status, initial.is_active = "interrupted", False
    wrong_agent_resume = _child_run(db, job, source="resume", parent_id=initial.id)
    wrong_agent_resume.agent_id = job.parent_agent_id
    wrong_agent_resume.created_at = initial.created_at + timedelta(minutes=1)
    await db.commit()
    prefix = f"/api/conversations/{job.child_conversation_id}"
    response = await client.get(f"{prefix}/messages")
    assert response.status_code == 200, response.text
    assert response.json()["latest_run"]["id"] == str(initial.id)
    assert response.json()["active_run"]["id"] == str(initial.id)
