"""An approval recovered without live credentials uses its exact persisted display."""

from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service
from app.models.conversation_run import ConversationRun
from tests.authz.g03_support import approval_payload, seed_conversation, seed_interrupted

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("persisted", ["same", "different-arguments", "different-id"])
async def test_recovery_requires_the_offered_action_to_match_before_inbox_can_approve(
    db: AsyncSession, persisted: str
) -> None:
    conversation = await seed_conversation(db)
    raw = approval_payload()
    raw["action_requests"][1]["description"] = "archived secret description"
    shown = deepcopy(raw)
    shown["action_requests"][1]["description"] = "<redacted>"
    if persisted == "different-arguments":
        shown["action_requests"][1]["args"] = {"to": "another-recipient"}
    run_id = await seed_interrupted(
        db,
        conversation,
        shown,
        iid="other" if persisted == "different-id" else "expected",
    )
    run = await db.get(ConversationRun, run_id)
    assert run is not None
    run.tenant_id, run.org_id = conversation.tenant_id, conversation.org_id
    await db.commit()
    [row] = await service.record_pending(
        db,
        run,
        [service.PendingInterrupt("expected", raw)],
        origin=service.RunOrigin(source="chat"),
        secret_values=None,
    )
    assert row.args_digest == service.args_digest(raw)
    assert "archived secret description" not in str(row.actions)
    action = row.actions[1]
    if persisted == "same":
        assert action["description"] == "<redacted>"
        assert action["args"] == shown["action_requests"][1]["args"]
        assert "approve" in action["allowed_decisions"]
    else:
        assert action["args"] == "<redacted>"
        assert action["description"] is None
        assert action["allowed_decisions"] == ["reject"]
