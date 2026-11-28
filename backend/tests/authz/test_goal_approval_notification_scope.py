"""A canonical goal approval link is private even after the named work disappears."""

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval_request import ApprovalRequest
from app.models.conversation_goal import ConversationGoal
from app.models.conversation_run import ConversationRun
from tests.test_chat_notification_approval import _drain, _record, _wait_for_approval, waiting_goal

__all__ = ["waiting_goal"]


@pytest.mark.parametrize("change", ["goal_version", "request_deleted"])
async def test_goal_approval_read_hides_obsolete_notice_target(
    client: AsyncClient,
    db: AsyncSession,
    waiting_goal: tuple[ConversationGoal, ConversationRun],
    change: str,
) -> None:
    goal, run = waiting_goal
    await _wait_for_approval(db, goal, run)
    request = await _record(db, run)
    await _drain(db)
    [item] = (await client.get("/api/notifications")).json()["items"]
    assert item["target"] == {
        "type": "approval_request",
        "id": str(request.id),
        "name": None,
        "accessible": True,
    }
    assert item["link"] == "/approvals"
    assert "chat_goal" not in item["payload"]
    assert (await client.post(f"/api/notifications/{item['id']}/read")).status_code == 204
    if change == "goal_version":
        goal.version += 1
    else:
        await db.execute(delete(ApprovalRequest).where(ApprovalRequest.id == request.id))
    await db.commit()
    [hidden] = (await client.get("/api/notifications")).json()["items"]
    assert hidden["id"] == item["id"] and hidden["read_at"] is not None
    assert hidden["link"] is None and hidden["actor"] is None and hidden["payload"] == {}
    assert hidden["target"] == {
        "type": "approval_request",
        "id": None,
        "name": None,
        "accessible": False,
    }
