"""The run status tab (feature plan v2 순번 13, 17.2): my runs and my agents' runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.user import User
from tests.authz.g03_support import approval_payload, seed_conversation, seed_interrupted
from tests.conftest import TEST_USER_ID


def _run(
    conversation: Conversation, status: str, *, user_id: uuid.UUID = TEST_USER_ID, agent_id=None
):
    return ConversationRun(
        conversation_id=conversation.id,
        agent_id=agent_id or conversation.agent_id,
        user_id=user_id,
        source="chat",
        status=status,
        is_active=status in {"queued", "running", "canceling"},
        tenant_id=conversation.tenant_id,
        org_id=conversation.org_id,
        run_source="CHAT",
        error_code="runtime_error" if status == "failed" else None,
    )


@pytest.mark.asyncio
async def test_the_tab_groups_my_runs_and_hides_other_peoples_conversations(
    client: AsyncClient, db: AsyncSession
) -> None:
    mine = await seed_conversation(db)
    other_user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@x.dev", name="Other")
    db.add(other_user)
    await db.flush()
    others_conversation = Conversation(
        agent_id=mine.agent_id,
        title="theirs",
        org_id=mine.org_id,
        tenant_id=mine.tenant_id,
        user_id=other_user.id,
        agent_name_snapshot="Protocol Agent",
        last_activity_at=datetime.now(UTC),
    )
    foreign_agent = Agent(
        user_id=other_user.id,
        name="Not mine",
        system_prompt="x",
        model_id=(await db.get(Agent, mine.agent_id)).model_id,  # type: ignore[union-attr]
        org_id=mine.org_id,
        tenant_id=mine.tenant_id,
    )
    db.add_all([others_conversation, foreign_agent])
    await db.flush()
    running = _run(mine, "running")
    theirs = _run(others_conversation, "running", user_id=other_user.id)
    not_mine = _run(others_conversation, "failed", user_id=other_user.id, agent_id=foreign_agent.id)
    failed = _run(mine, "failed")
    db.add_all([running, theirs, not_mine, failed])
    await db.commit()
    waiting = await db.get(ConversationRun, await seed_interrupted(db, mine, approval_payload()))
    assert waiting is not None
    waiting.tenant_id, waiting.org_id = mine.tenant_id, mine.org_id
    await db.commit()
    await service.record_pending(
        db,
        waiting,
        [service.PendingInterrupt("intr-1", approval_payload())],
        origin=service.RunOrigin(),
    )
    await db.commit()

    by_state = {
        state: (await client.get(f"/api/approvals/runs?state={state}")).json()["items"]
        for state in ("running", "waiting", "failed")
    }

    running_ids = {item["run_id"]: item for item in by_state["running"]}
    assert set(running_ids) == {str(running.id), str(theirs.id)}
    assert running_ids[str(running.id)]["conversation_id"] == str(mine.id)
    assert running_ids[str(theirs.id)]["mine"] is False
    assert running_ids[str(theirs.id)]["conversation_id"] is None  # not my conversation
    assert [item["run_id"] for item in by_state["waiting"]] == [str(waiting.id)]
    assert [item["run_id"] for item in by_state["failed"]] == [str(failed.id)]
    assert by_state["failed"][0]["error_code"] == "runtime_error"
