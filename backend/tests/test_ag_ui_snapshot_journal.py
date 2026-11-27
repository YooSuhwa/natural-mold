"""A snapshot can resume only from public frames it actually incorporated."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import ag_ui_v1
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.protocol_events import stored_protocol_event
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import ag_ui_snapshot_journal, conversation_run_service, trace_storage
from app.services.ag_ui_journal import JournalEntry, JournalPage, read_journal_page
from app.services.ag_ui_read_cursor import matches_source, parse_cursor
from app.services.ag_ui_read_target import resolve_read_target
from app.services.ag_ui_snapshot_journal import (
    SnapshotRunReplay,
    _ObservedOwners,
    replay_snapshot_run,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.test_conversation_run_message_links import _create_run, _seed_conversation


def _answer(n: int, run_id: UUID, thread_id: UUID, text: str) -> dict[str, Any]:
    return dict(
        stored_protocol_event(
            run_id=str(run_id),
            thread_id=str(thread_id),
            id=f"source-{n}",
            seq=n * 10,
            method="messages",
            data={"id": f"answer-{n}", "type": "ai", "content": text},
        )
    )


async def _running_chat(db: AsyncSession) -> tuple[Conversation, ConversationRun, OrgContext]:
    agent, chat = await _seed_conversation(db)
    run = await _create_run(db, conversation=chat, agent=agent)
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="worker")
    return chat, run, unit_context(chat.user_id)


@pytest.mark.asyncio
async def test_legacy_input_and_native_answer_reopen_with_exact_terminal_cursor(
    db: AsyncSession,
) -> None:
    chat, run, context = await _running_chat(db)
    start = {
        "id": "old-entry",
        "event": "message_start",
        "data": {
            "id": str(run.id),
            "input": {
                "messages": [
                    {"role": "system", "content": "private instruction"},
                    {"role": "user", "content": "earlier turn"},
                    {"role": "user", "id": "submit", "content": "my submission"},
                ]
            },
        },
    }
    record = await trace_storage.append_events(
        db,
        conversation_id=chat.id,
        assistant_msg_id=str(run.id),
        events_chunk=[start, _answer(2, run.id, chat.id, "answer")],
    )
    await trace_storage.finalize_turn(db, assistant_msg_id=str(run.id))
    await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()

    statements: list[str] = []

    def observe(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement.lstrip().split()[0].upper())

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", observe)
    try:

        async def reopen() -> SnapshotRunReplay:
            return await replay_snapshot_run(
                db,
                conversation=chat,
                context=context,
                run=run,
                public_id=str(run.id),
                parent_public_id=None,
                secret_values=(),
            )

        first, second = await reopen(), await reopen()
        target = await resolve_read_target(db, chat, context, str(run.id))
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert first == second
    assert set(statements) == {"SELECT"}
    assert first.complete and first.cursor is not None
    assert first.legacy_users == [{"id": "submit", "role": "user", "content": "my submission"}]
    assert [(m["role"], m["content"]) for m in first.messages] == [("assistant", "answer")]
    pos = parse_cursor(first.cursor, target.scope)
    assert (pos.kind, pos.ordinal) == ("terminal", 3)
    assert record is not None and matches_source(pos, source_id="finish", generation=str(record.id))


@pytest.mark.asyncio
async def test_foreign_or_missing_physical_binding_never_reads_a_journal(db: AsyncSession) -> None:
    chat, run, context = await _running_chat(db)
    _, other = await _seed_conversation(db, user_id=uuid4(), email="foreign@example.test")
    await db.commit()
    errors = []
    for conversation, public_id in [(other, str(run.id)), (chat, str(uuid4()))]:
        with pytest.raises(AppError) as denied:
            await replay_snapshot_run(
                db,
                conversation=conversation,
                context=context,
                run=run,
                public_id=public_id,
                parent_public_id=None,
                secret_values=(),
            )
        errors.append((denied.value.status, denied.value.code))
    assert errors[0] == errors[1] and errors[0][0] == 404


def test_hidden_or_conflicting_owners_never_restore_a_root_or_child_control() -> None:
    visible, hidden = ("task:a",), ("task:b",)
    child = ag_ui_v1.public_subagent_id("thread", visible)
    result = SnapshotRunReplay(
        visibility=ChildVisibility(False, frozenset(), frozenset()),
        subagents=[
            {"subagentRunId": child, "parentToolCallId": "call-a", "parentMessageId": "msg-a"}
        ],
    )
    owners = _ObservedOwners(
        namespaces={visible, hidden},
        pending={
            "visible": {visible},
            "hidden": {hidden},
            "ambiguous": {visible, hidden},
            "root": {()},
        },
    )
    owners.bind(result, "thread", {child: "approved-task"})
    assert result.child_namespaces == {visible: child}
    assert result.pending_namespaces == {"visible": visible}
    assert result.unresolved_pending_ids == {"hidden", "ambiguous"}
    assert result.subagents[0]["childTaskId"] == "approved-task"


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_second_page", [False, True])
async def test_two_page_snapshot_obeys_first_committed_horizon_or_discards_a_gap(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, lose_second_page: bool
) -> None:
    chat, run, context = await _running_chat(db)
    await trace_storage.append_events(
        db,
        conversation_id=chat.id,
        assistant_msg_id=str(run.id),
        events_chunk=[_answer(1, run.id, chat.id, "first"), _answer(2, run.id, chat.id, "second")],
    )
    await db.commit()

    async def page(session: AsyncSession, **kwargs: Any) -> JournalPage:
        if kwargs["after"] == 0:
            return await read_journal_page(session, limit=1, **kwargs)
        if lose_second_page:
            # Lost or compacted storage after the first public frame was folded.
            return JournalPage(
                generation="changed",
                entries=(),
                has_more=False,
                gap=True,
                trace_status="streaming",
                high_water=2,
            )
        await trace_storage.append_events(
            db,
            conversation_id=chat.id,
            assistant_msg_id=str(run.id),
            events_chunk=[_answer(3, run.id, chat.id, "after request")],
        )
        await db.commit()
        return await read_journal_page(session, **kwargs)

    monkeypatch.setattr(ag_ui_snapshot_journal, "read_journal_page", page)
    result = await replay_snapshot_run(
        db,
        conversation=chat,
        context=context,
        run=run,
        public_id=str(run.id),
        parent_public_id=None,
        secret_values=(),
    )
    if lose_second_page:
        assert not result.complete and result.cursor is None
        assert result.messages == [] and result.legacy_users == []
    else:
        assert result.complete and result.cursor is not None
        assert [m["content"] for m in result.messages] == ["first", "second"]
        target = await resolve_read_target(db, chat, context, str(run.id))
        position = parse_cursor(result.cursor, target.scope)
        assert (position.kind, position.ordinal) == ("event", 2)


def test_native_child_path_restores_observed_lane_without_exposing_an_unrelated_child() -> None:
    visible, hidden = ("tool:one",), ("tool:two",)
    child = ag_ui_v1.public_subagent_id("thread", visible)
    events = (
        stored_protocol_event(
            thread_id="thread",
            run_id="run",
            seq=1,
            method="subagents",
            data={"path": list(visible), "status": "started"},
        ),
        stored_protocol_event(
            thread_id="thread",
            run_id="run",
            seq=2,
            method="input.requested",
            namespace=list(visible),
            data={"id": "pending-1"},
        ),
        stored_protocol_event(
            thread_id="thread",
            run_id="run",
            seq=3,
            method="input.requested",
            namespace=list(hidden),
            data={"id": "pending-2"},
        ),
    )
    owners = _ObservedOwners()
    owners.observe(
        JournalPage("gen", (JournalEntry(1, "source", events),), False, False, "streaming", 1)
    )
    result = SnapshotRunReplay(
        visibility=ChildVisibility(False, frozenset(), frozenset()),
        subagents=[{"subagentRunId": child}],
    )
    owners.bind(result, "thread", {child: "unproved-database-handle"})
    assert "childTaskId" not in result.subagents[0]
    assert result.child_namespaces == {visible: child}
    assert result.pending_namespaces == {"pending-1": visible}
    assert result.unresolved_pending_ids == {"pending-2"}
