"""Snapshot history of consumed tool decisions is scoped to its real resume lineage."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.approvals import service as approvals
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.user import User
from app.services.ag_ui_snapshot_decision_ledger import (
    ConsumedApprovalDecision,
    load_consumed_approval_decisions,
)
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from tests.authz.unit_scope_fixture import unit_context
from tests.test_conversation_run_message_links import _seed_conversation

pytestmark = pytest.mark.asyncio

SLOT = "review-action-group"
PRIVATE = "private meeting document"
ACTIONS: dict[str, Any] = {
    "action_requests": [
        {"name": "find_notes", "args": {"folder": PRIVATE}},
        {"name": "send_notes", "args": {"folder": PRIVATE}},
    ],
    "review_configs": [
        {"action_name": name, "allowed_decisions": ["approve", "edit", "reject"]}
        for name in ("find_notes", "send_notes")
    ],
}
DECISIONS = ["approve", "reject"]


@dataclass
class History:
    conversation: Conversation
    context: OrgContext
    parents: list[ConversationRun]
    resumed: ConversationRun
    records: list[ApprovalRequest]

    async def read(
        self,
        db: AsyncSession,
        branch: Collection[UUID] | None = None,
    ) -> list[ConsumedApprovalDecision]:
        return await load_consumed_approval_decisions(
            db,
            conversation=self.conversation,
            context=self.context,
            branch_run_ids=[row.id for row in [*self.parents, self.resumed]]
            if branch is None
            else branch,
        )


async def _accepted(
    db: AsyncSession, conversation: Conversation, context: OrgContext, parent: UUID | None
) -> ConversationRun:
    assert conversation.agent_id is not None
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation.id,
            conversation.agent_id,
            context,
            "chat",
            None,
            run_source="resume" if parent is not None else "start",
            parent_run_id=parent,
            interrupt_id=SLOT if parent is not None else None,
        ),
    )
    await db.commit()
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    return run


async def _history(
    db: AsyncSession,
    *,
    duplicates: bool = False,
    channel: str = "chat",
) -> History:
    _, conversation = await _seed_conversation(db)
    context = unit_context(conversation.user_id)
    parents: list[ConversationRun] = []
    records: list[ApprovalRequest] = []
    for _ in range(2 if duplicates else 1):
        parent = await _accepted(db, conversation, context, parents[-1].id if parents else None)
        parent.status, parent.is_active, parent.interrupt_id = "interrupted", False, SLOT
        parents.append(parent)
        [record] = await approvals.record_pending(
            db,
            parent,
            [approvals.PendingInterrupt(SLOT, ACTIONS)],
            origin=approvals.RunOrigin(agent_name="Research", source="chat"),
        )
        records.append(record)
        await db.commit()
    resumed = await _accepted(db, conversation, context, parents[-1].id)
    for parent in parents:
        [claimed] = await approvals.claim(
            db,
            approvals.Claim(
                run_id=parent.id,
                decisions={SLOT: DECISIONS},
                channel=channel,
                actor_id=None if channel == "expiry" else context.user_id,
                resumed_run_id=resumed.id,
            ),
        )
        assert claimed.resumed_run_id == resumed.id
    resumed.status, resumed.is_active = "completed", False
    await db.commit()
    return History(conversation, context, parents, resumed, records)


@pytest.mark.parametrize(("duplicates", "channel"), [(False, "chat"), (True, "inbox")])
async def test_actual_claims_recover_one_minimal_group_from_the_selected_resume(
    db: AsyncSession,
    duplicates: bool,
    channel: str,
) -> None:
    history = await _history(db, duplicates=duplicates, channel=channel)
    pending = User(email="unflushed-decision@example.test", name="unflushed")
    db.add(pending)

    def assert_read_only(state: ORMExecuteState) -> None:
        assert state.is_select
        assert getattr(state.statement, "_for_update_arg", None) is None

    def forbid_change(*_: object) -> None:
        pytest.fail("history lookup changed the transaction")

    listeners = [
        ("do_orm_execute", assert_read_only),
        ("before_flush", forbid_change),
        ("after_commit", forbid_change),
        ("after_rollback", forbid_change),
    ]
    for key, callback in listeners:
        event.listen(db.sync_session, key, callback)
    try:
        [decision] = await history.read(db)
    finally:
        for key, callback in listeners:
            event.remove(db.sync_session, key, callback)

    assert decision.parent_run_id == history.parents[-1].id
    assert decision.resumed_run_id == history.resumed.id
    assert decision.source_run_ids == tuple(row.id for row in history.parents)
    assert decision.interrupt_id == SLOT
    assert decision.namespace == ()
    assert decision.args_digest == approvals.args_digest(ACTIONS)
    assert [(a.index, a.tool_name, a.decision) for a in decision.actions] == [
        (0, "find_notes", "approve"),
        (1, "send_notes", "reject"),
    ]
    assert pending in db.new
    payload = repr(asdict(decision))
    assert PRIVATE not in payload and str(history.context.user_id) not in payload
    assert "allowed_decisions" not in payload and "decided_by" not in payload


async def test_only_branch_members_and_user_consumed_decisions_are_visible(
    db: AsyncSession,
) -> None:
    history = await _history(db, channel="expiry")
    assert history.records[0].status == "expired"
    assert await history.read(db) == []
    assert await history.read(db, [history.parents[0].id]) == []


@pytest.mark.parametrize("status", ["failed", "canceled", "stale"])
async def test_unconfirmed_resumed_dispatch_is_omitted(db: AsyncSession, status: str) -> None:
    history = await _history(db)
    history.resumed.status = status
    await db.commit()

    assert await history.read(db) == []


@pytest.mark.parametrize(
    "change",
    [
        "foreign-decider",
        "late",
        "before-created",
        "version",
        "wrong-source",
        "cardinality",
        "decision-kind",
        "sibling",
        "foreign-dispatch",
        "parent-completed",
    ],
)
async def test_incomplete_or_conflicting_claim_evidence_is_never_reported(
    db: AsyncSession,
    change: str,
) -> None:
    history = await _history(db)
    [record] = history.records
    [parent] = history.parents
    if change == "foreign-decider":
        other = User(email="foreign-decider@example.test", name="another decider")
        db.add(other)
        await db.flush()
        record.decided_by = other.id
    elif change == "late":
        assert record.decided_at is not None
        record.expires_at = record.decided_at - timedelta(seconds=1)
    elif change == "before-created":
        record.decided_at = approvals.aware(record.created_at) - timedelta(seconds=1)
    elif change == "version":
        record.decision_no += 1
    elif change == "wrong-source":
        record.source = "api"
    elif change == "cardinality":
        record.decisions = ["approve"]
    elif change == "decision-kind":
        record.decisions = ["approve", "feedback"]
    elif change == "sibling":
        assert await history.read(db, [uuid4(), parent.id]) == []
        return
    elif change == "foreign-dispatch":
        history.resumed.run_source, history.resumed.invoker_type = "API", "api"
    elif change == "parent-completed":
        parent.status = "completed"
    await db.commit()

    assert await history.read(db) == []


@pytest.mark.parametrize("conflict", ["decisions", "digest", "namespace"])
async def test_conflicting_records_for_the_same_native_interrupt_do_not_pick_a_winner(
    db: AsyncSession,
    conflict: str,
) -> None:
    history = await _history(db, duplicates=True)
    earlier, _ = history.records
    if conflict == "decisions":
        earlier.decisions = list(reversed(DECISIONS))
    elif conflict == "digest":
        earlier.args_digest = approvals.digest("other action arguments")
    else:
        earlier.namespace = ["tools:sibling"]
    await db.commit()

    assert await history.read(db) == []


async def test_unbound_child_claim_keeps_its_decision_out_of_the_root_history(
    db: AsyncSession,
) -> None:
    history = await _history(db)
    [record] = history.records
    record.source = "subagent"
    record.namespace = ["tools:delegate", "tools:store"]
    await db.commit()

    assert await history.read(db) == []


async def test_valid_decision_identifiers_cannot_be_read_in_another_private_conversation(
    db: AsyncSession,
) -> None:
    history = await _history(db)
    _, other = await _seed_conversation(db, user_id=uuid4(), email="other-ledger@example.test")
    await db.commit()
    other_context = unit_context(other.user_id)
    known_run_ids = [history.parents[0].id, history.resumed.id]
    assert (
        await load_consumed_approval_decisions(
            db,
            conversation=other,
            context=other_context,
            branch_run_ids=known_run_ids,
        )
        == []
    )
    with pytest.raises(AppError) as denied:
        await load_consumed_approval_decisions(
            db,
            conversation=history.conversation,
            context=other_context,
            branch_run_ids=known_run_ids,
        )
    assert denied.value.status == 404
