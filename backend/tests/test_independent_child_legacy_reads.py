from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.message_event import MessageEvent
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from app.services.chat_resource_context_sources import ResourceContextScope, owned_conversation
from tests.authz.test_independent_job_authority import delegation as delegation

pytestmark = pytest.mark.asyncio
Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


@pytest.mark.parametrize("loss", ["parent_deleted", "edge_removed"])
async def test_direct_child_reads_recheck_delegation(
    client: AsyncClient, db: AsyncSession, delegation: Delegation, loss: str
) -> None:
    context, parent_run, child_run, job = delegation
    child_id, run_id = child_run.conversation_id, child_run.id
    scope = ResourceContextScope(context, job.parent_agent_id, parent_run.conversation_id)
    base = f"/api/conversations/{child_id}"
    metadata = await client.get(base)
    assert metadata.status_code == 200
    assert metadata.json()["title"] == "Independent"
    active = await client.get(f"{base}/runs/active")
    assert active.status_code == 200 and active.json()["id"] == str(run_id)
    assert (await owned_conversation(db, scope, child_id)).id == child_id

    if loss == "parent_deleted":
        await db.execute(delete(Conversation).where(Conversation.id == job.parent_conversation_id))
    else:
        await db.execute(
            delete(AgentSubAgentLink).where(
                AgentSubAgentLink.parent_agent_id == job.parent_agent_id,
                AgentSubAgentLink.sub_agent_id == job.child_agent_id,
            )
        )
    await db.commit()
    assert await db.scalar(select(Conversation.id).where(Conversation.id == child_id)) == child_id
    for route in (
        base,
        f"{base}/messages",
        f"{base}/history",
        f"{base}/traces",
        f"{base}/runs/active",
        f"{base}/runs/{run_id}",
        f"{base}/runs/{run_id}/stream",
        f"{base}/runs/{run_id}/ag-ui-stream",
        f"{base}/stream?run_id={run_id}",
    ):
        result = await client.get(route)
        assert result.status_code == 404, route
        assert "Independent" not in result.text
    history = await client.post(f"/threads/{child_id}/history", json={})
    assert history.status_code == 404
    with pytest.raises(ResourceContextNotFoundError):
        await owned_conversation(db, scope, child_id)


async def test_ordinary_chat_read_skips_independent_authority(
    client: AsyncClient, delegation: Delegation, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, parent_run, _, _ = delegation
    guard = AsyncMock(side_effect=AssertionError("ordinary conversation queried delegation"))
    monkeypatch.setattr(
        "app.services.independent_job_runs.can_read_independent_child_conversation", guard
    )
    base = f"/api/conversations/{parent_run.conversation_id}"
    assert (await client.get(base)).status_code == 200
    assert (await client.get(f"{base}/runs/active")).status_code == 200
    assert (await client.get(f"/api/conversations/{uuid.uuid4()}")).status_code == 404
    guard.assert_not_awaited()


async def test_owner_can_erase_inaccessible_retained_child(
    client: AsyncClient, db: AsyncSession, delegation: Delegation, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, child_run, job = delegation
    child_id = job.child_conversation_id
    child_run.status = "completed"
    child_run.is_active = False
    await db.execute(delete(Conversation).where(Conversation.id == job.parent_conversation_id))
    await db.commit()
    assert (await client.get(f"/api/conversations/{child_id}")).status_code == 404
    monkeypatch.setattr("app.agent_runtime.checkpointer.delete_thread", AsyncMock())
    result = await client.delete(f"/api/conversations/{child_id}")
    assert result.status_code == 204
    assert result.content == b""
    assert await db.scalar(select(Conversation.id).where(Conversation.id == child_id)) is None


async def test_run_metadata_and_replays_are_bound_to_the_registered_child_run(
    client: AsyncClient, db: AsyncSession, delegation: Delegation
) -> None:
    context, _, child_run, job = delegation
    child_run.is_active = False
    child_run.status = "completed"
    unrelated_run = ConversationRun(
        conversation_id=job.child_conversation_id,
        agent_id=job.child_agent_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        user_id=context.user_id,
        source="start",
        run_source="CHAT",
        status="completed",
    )
    db.add(unrelated_run)
    await db.flush()
    for run in (child_run, unrelated_run):
        db.add(
            MessageEvent(
                conversation_id=job.child_conversation_id,
                assistant_msg_id=str(run.id),
                events=[{"id": f"{run.id}-1", "event": "message_end", "data": {"content": "done"}}],
                status="completed",
            )
        )
    await db.commit()
    base = f"/api/conversations/{job.child_conversation_id}"
    for run_id, expected in ((child_run.id, 200), (unrelated_run.id, 404)):
        for path in (
            f"{base}/runs/{run_id}",
            f"{base}/runs/{run_id}/stream",
            f"{base}/runs/{run_id}/ag-ui-stream",
            f"{base}/stream?run_id={run_id}",
        ):
            response = await client.get(path)
            assert response.status_code == expected, path

    unrelated_run.is_active = True
    unrelated_run.status = "running"
    await db.commit()
    active = await client.get(f"{base}/runs/active")
    assert active.status_code == 200 and active.json() is None
    detail = await client.get(base)
    assert detail.status_code == 200 and detail.json()["active_run"] is None
