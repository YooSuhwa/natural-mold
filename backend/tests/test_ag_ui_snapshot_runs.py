from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.models.organization import Organization
from app.models.user import User
from app.services import ag_ui_snapshot_runs as runs
from app.services.ag_ui_snapshot_history import SnapshotHistory, SnapshotMessageRef
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.test_conversation_run_message_links import _seed_conversation
from tests.test_independent_child_read_scope import Delegation, _child_run

pytestmark = pytest.mark.asyncio


@pytest.fixture
def public_ids(monkeypatch: pytest.MonkeyPatch) -> dict[UUID, tuple[str, str | None]]:
    mapped: dict[UUID, tuple[str, str | None]] = {}

    async def receipts(
        db: AsyncSession, context: OrgContext, conversation_id: UUID, run_ids: Collection[UUID]
    ) -> dict[UUID, tuple[str, str | None]]:
        return {key: mapped[key] for key in run_ids if key in mapped}

    monkeypatch.setattr(runs, "load_run_identities", receipts)
    return mapped


def _run(
    db: AsyncSession, conversation: Conversation, order: int, *, status: str = "completed"
) -> ConversationRun:
    row = ConversationRun(
        id=uuid4(),
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        agent_id=conversation.agent_id,
        tenant_id=conversation.tenant_id,
        org_id=conversation.org_id,
        status=status,
        is_active=status in {"running", "queued"},
        source="chat",
        created_at=datetime(2026, 1, 1) + timedelta(seconds=order),
    )
    db.add(row)
    return row


def _trace(db: AsyncSession, row: ConversationRun, *wire_ids: str) -> None:
    db.add(
        MessageEvent(
            conversation_id=row.conversation_id,
            assistant_msg_id=str(row.id),
            linked_message_ids=[str(parse_msg_id(key, row.conversation_id, 0)) for key in wire_ids],
            events=[],
        )
    )


def _message(wire_id: str, role: str, index: int, checkpoint: str) -> SnapshotMessageRef:
    return SnapshotMessageRef(
        {"id": wire_id, "role": role, "content": "public text"}, index, checkpoint, None
    )


async def test_legacy_scope_is_read_only_and_canonical_links_keep_the_public_id(
    db: AsyncSession, public_ids: dict[UUID, tuple[str, str | None]]
) -> None:
    _, conversation = await _seed_conversation(db)
    legacy = _run(db, conversation, 1)
    legacy.org_id = legacy.tenant_id = None
    current = _run(db, conversation, 2)
    other_org = Organization(tenant_id=TEST_TENANT_ID, slug="other", name="Other")
    db.add(other_org)
    await db.flush()
    misplaced = _run(db, conversation, 3)
    misplaced.org_id = other_org.id
    wire_id = "provider-message-old"
    canonical = str(parse_msg_id(wire_id, conversation.id, 0))
    _trace(db, legacy, wire_id)
    _trace(db, misplaced, "foreign-message")
    await db.commit()
    pending = User(email="pending-snapshot@example.test", name="unflushed")
    db.add(pending)
    public_ids[current.id] = ("client-run-42", None)

    snapshot = await runs.snapshot_runs(
        db,
        conversation,
        unit_context(conversation.user_id),
        SnapshotHistory(None, (), "legacy"),
        [wire_id, canonical, "foreign-message"],
    )

    assert [row.id for row in snapshot.candidates] == [legacy.id, current.id]
    assert snapshot.public_ids == {legacy.id: str(legacy.id), current.id: "client-run-42"}
    assert snapshot.message_runs[wire_id] == snapshot.message_runs[canonical] == legacy.id
    assert "foreign-message" not in snapshot.message_runs
    assert pending in db.new


async def test_stamped_fork_keeps_selected_messages_and_excludes_the_sibling_journal(
    db: AsyncSession, public_ids: dict[UUID, tuple[str, str | None]]
) -> None:
    _, conversation = await _seed_conversation(db)
    prefix = _run(db, conversation, 1)
    sibling = _run(db, conversation, 2)
    selected = _run(db, conversation, 3)
    _trace(db, sibling, "shared-input")
    await db.commit()
    history = SnapshotHistory(
        "selected",
        (
            _message("shared-input", "user", 0, "source"),
            _message("reply", "assistant", 1, "selected"),
        ),
        "checkpoint",
        ("source", "selected"),
        True,
        {"source": prefix.id, "selected": selected.id},
        frozenset({prefix.id, sibling.id}),
    )
    snapshot = await runs.snapshot_runs(
        db,
        conversation,
        unit_context(conversation.user_id),
        history,
        ["shared-input", "reply"],
    )

    assert selected.id in snapshot.branch_verified
    assert not {prefix.id, sibling.id} & snapshot.branch_verified
    assert sibling.id not in {row.id for row in snapshot.candidates}
    assert snapshot.message_runs["reply"] == selected.id
    assert snapshot.message_runs.get("shared-input") != sibling.id


@pytest.mark.parametrize("status", ["running", "queued", "failed"])
async def test_uncheckpointed_turn_with_exact_tip_cannot_follow_a_later_branch_switch(
    db: AsyncSession, public_ids: dict[UUID, tuple[str, str | None]], status: str
) -> None:
    _, conversation = await _seed_conversation(db)
    row = _run(db, conversation, 1, status=status)
    row.metadata_json = {"checkpoint_id": "left"}
    _trace(db, row, "same-provider")
    await db.commit()
    context = unit_context(conversation.user_id)
    left = SnapshotHistory("left", (), "checkpoint", ("common", "left"), True)
    right = SnapshotHistory(
        "right",
        (_message("same-provider", "assistant", 0, "right"),),
        "checkpoint",
        ("common", "right"),
        True,
    )

    active_branch = await runs.snapshot_runs(db, conversation, context, left, [])
    switched = await runs.snapshot_runs(db, conversation, context, right, ["same-provider"])

    assert row.id in active_branch.branch_verified
    assert row.id not in switched.branch_verified
    assert "same-provider" not in switched.message_runs


async def test_child_read_requires_a_permitted_run_even_with_a_real_checkpoint_stamp(
    db: AsyncSession, public_ids: dict[UUID, tuple[str, str | None]], delegation: Delegation
) -> None:
    context, _, original, job = delegation
    original.status, original.is_active = "interrupted", False
    resumed = _child_run(db, job, source="resume", parent_id=original.id)
    unrelated = _child_run(db, job, source="chat", parent_id=None)
    await db.commit()
    child = await db.get(Conversation, job.child_conversation_id)
    assert child is not None
    history = SnapshotHistory(
        "c3",
        (
            _message("submitted", "user", 0, "c1"),
            _message("result", "assistant", 1, "c2"),
            _message("unbound", "assistant", 2, "c3"),
        ),
        "checkpoint",
        ("c1", "c2", "c3"),
        checkpoint_run_ids={"c1": original.id, "c2": resumed.id, "c3": unrelated.id},
    )
    snapshot = await runs.snapshot_runs(
        db, child, context, history, ["submitted", "result", "unbound"]
    )

    assert snapshot.independent
    assert set(snapshot.public_ids) == {original.id, resumed.id}
    assert {row.id for row in snapshot.candidates} == {original.id, resumed.id}
    assert snapshot.message_runs == {"submitted": original.id, "result": resumed.id}


async def test_requesting_a_run_from_another_conversation_reveals_no_identifier(
    db: AsyncSession, public_ids: dict[UUID, tuple[str, str | None]]
) -> None:
    _, conversation = await _seed_conversation(db)
    _, other = await _seed_conversation(db, user_id=uuid4(), email="other-snapshot@example.test")
    unrelated = _run(db, other, 1)
    await db.commit()
    with pytest.raises(AppError) as denied:
        await runs.snapshot_runs(
            db,
            conversation,
            unit_context(conversation.user_id),
            SnapshotHistory(None, (), "legacy"),
            [],
            run_id=unrelated.id,
        )
    assert denied.value.status == 404


async def test_paging_equal_timestamps_keeps_order_and_honors_receipt_only_checkpoint(
    db: AsyncSession,
    public_ids: dict[UUID, tuple[str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, conversation = await _seed_conversation(db)
    monkeypatch.setattr(runs, "_PAGE_SIZE", 1)
    identities = AsyncMock(wraps=runs.load_run_identities)
    monkeypatch.setattr(runs, "load_run_identities", identities)
    same_time = [_run(db, conversation, 1) for _ in range(3)]
    first, middle, last = sorted(same_time, key=lambda row: row.id)
    await db.commit()
    public_ids[first.id] = ("selected-first", "left")
    public_ids[middle.id] = ("sibling-middle", "right")
    public_ids[last.id] = ("selected-last", "left")

    snapshot = await runs.snapshot_runs(
        db,
        conversation,
        unit_context(conversation.user_id),
        SnapshotHistory("left", (), "checkpoint", ("fork", "left"), True),
        [],
    )

    assert [row.id for row in snapshot.candidates] == [first.id, last.id]
    assert [list(call.args[3]) for call in identities.await_args_list] == [
        [first.id],
        [middle.id],
        [last.id],
    ]
    assert snapshot.branch_verified == {first.id, last.id}
    assert snapshot.public_ids == {first.id: "selected-first", last.id: "selected-last"}
