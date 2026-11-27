"""Read-time approval capabilities apply to the entire current resume batch."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.exceptions import NotFoundError
from app.services import (
    ag_ui_legacy_child_binding,
    ag_ui_pending_checkpoint,
    ag_ui_read_pending,
    conversation_run_service,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.test_conversation_run_message_links import _create_run, _seed_conversation


@pytest.mark.asyncio
async def test_current_batch_authority_expiry_and_checkpoint_identity(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, chat = await _seed_conversation(db)
    run = await _create_run(db, agent=agent, conversation=chat)
    await conversation_run_service.transition_run(db, run, "running")
    await conversation_run_service.transition_run(db, run, "interrupted")
    await db.commit()
    ctx = unit_context(chat.user_id)
    deadline = datetime.now(UTC) + timedelta(days=2)
    binding = ag_ui_legacy_child_binding.LegacyChildBinding(
        "real-child-role", "task-call", "tools:task-1|model:task-2", run.id
    )
    checkpoint = ag_ui_pending_checkpoint.PendingCheckpoint(
        "head",
        [
            {"id": "root", "ns": [], "value": {"type": "ask_user"}},
            {"id": "child", "ns": [], "value": {"subject": "child prompt"}},
        ],
        {"child": deadline},
        {"root": "ask_user", "child": "approval"},
        legacy_child_bindings={"child": binding},
    )
    read = AsyncMock(return_value=checkpoint)
    proof = AsyncMock(return_value=frozenset({"child"}))
    monkeypatch.setattr(ag_ui_read_pending, "read_pending_checkpoint", read)
    monkeypatch.setattr(ag_ui_read_pending, "read_pending_child_authority", proof)
    legacy = ChildVisibility(True, frozenset(), frozenset())
    hidden = ChildVisibility(False, frozenset(), frozenset())
    assert (
        await ag_ui_read_pending.constrain_legacy_child_visibility(db, chat, ctx, run, legacy)
        == legacy
    )

    current = await ag_ui_read_pending.read_proven_interrupts(
        db, chat, ctx, run, checkpoint_id="head"
    )
    assert current.actionable and current.legacy_child_ids == {"child"}
    assert current.legacy_bindings == {"child": binding}
    assert current.legacy_namespaces == {"child": ("tools:task-1", "model:task-2")}
    assert current.interrupts[1]["ns"] == ["tools:task-1", "model:task-2"]
    assert current.expires_at == {"child": deadline.isoformat()}
    assert not (
        await ag_ui_read_pending.read_proven_interrupts(db, chat, ctx, run, checkpoint_id="sibling")
    ).interrupts

    monkeypatch.setattr(ag_ui_read_pending.approvals, "now", lambda: deadline)
    expired = await ag_ui_read_pending.read_proven_interrupts(db, chat, ctx, run)
    assert not expired.actionable and not expired.legacy_child_ids
    assert (
        expired.legacy_bindings == {"child": binding} and expired.expires_at == current.expires_at
    )
    proof.side_effect = NotFoundError("CHILD_TASK_NOT_FOUND", "denied")
    checkpoint.interrupts[1]["ns"] = ["tools:task-1"]
    denied = await ag_ui_read_pending.read_proven_interrupts(db, chat, ctx, run)
    assert not denied.actionable and [item["id"] for item in denied.interrupts] == ["root"]
    assert not denied.legacy_namespaces and not denied.expires_at
    assert "child prompt" not in repr(denied)
    assert denied.child_authority_denied
    assert (
        await ag_ui_read_pending.constrain_legacy_child_visibility(db, chat, ctx, run, legacy)
        == hidden
    )

    newer = await _create_run(db, agent=agent, conversation=chat)
    await conversation_run_service.transition_run(db, newer, "running")
    await conversation_run_service.transition_run(db, newer, "interrupted")
    await db.commit()
    read.reset_mock()
    stale = await ag_ui_read_pending.read_proven_interrupts(db, chat, ctx, run)
    assert stale.checkpoint_id is None and not stale.interrupts
    read.assert_not_called()
    # A superseding approval cannot authorize child frames of the historical run.
    read.return_value = None
    assert (
        await ag_ui_read_pending.constrain_legacy_child_visibility(db, chat, ctx, run, legacy)
        == hidden
    )
    observed = read.await_args
    assert observed is not None and observed.kwargs["parent_run"].id == run.id
