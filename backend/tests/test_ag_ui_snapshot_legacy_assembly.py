from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.models.conversation_run import ConversationRun
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot
from app.services.ag_ui_snapshot_legacy_assembly import Pair, insert_legacy_transcript

ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)


def _at(day: int) -> datetime:
    return ORIGIN + timedelta(days=day)


def _run(day: int | None = None, *, active: bool = False) -> ConversationRun:
    return ConversationRun(
        id=uuid4(), created_at=_at(day) if day is not None else None, is_active=active
    )


def _row(
    mid: str,
    role: str = "user",
    *,
    day: int | None = None,
    run: ConversationRun | None = None,
    parent: str | None = None,
) -> Pair:
    return {"id": mid, "role": role, "content": mid}, AgUiSnapshotMessageLink(
        message_id=mid,
        physical_run_id=str(run.id) if run else None,
        parent_message_id=parent,
        created_at=_at(day) if day is not None else None,
    )


def _current(rows: list[Pair], tip: str | None = None) -> AssembledSnapshot:
    return AssembledSnapshot(
        messages=[item[0] for item in rows],
        message_links=[item[1] for item in rows],
        subagents=[],
        todos_by_owner={},
        active_tip_message_id=tip,
        unreliable_run_ids=set(),
    )


def test_old_turns_interleave_without_splitting_physical_runs_or_detaching_children() -> None:
    first, active = _run(2), _run(4, active=True)
    child, child_link = _row("child-message", "assistant", run=first, parent="agent-a")
    child["subagentRunId"] = "researcher"
    child_link = child_link.model_copy(update={"subagent_run_id": "researcher"})
    saved = [
        _row("input-a", run=first),
        _row("agent-a", "assistant", run=first, parent="input-a"),
        (child, child_link),
        _row("input-b", run=active, parent="agent-a"),
    ]
    merged = _current(saved, "input-b")
    merged.unreliable_run_ids.add(first.id)
    merged.todos_by_owner = {None: [{"id": "todo", "content": "work", "status": "pending"}]}
    merged.subagents = [{"subagentRunId": "researcher", "name": "helper", "status": "running"}]
    old = [
        _row("first-question", day=1),
        _row("first-answer", "assistant", day=1, parent="first-question"),
        _row("second-question", day=3),
        _row("second-answer", "assistant", day=3, parent="second-question"),
        _row("clock-skew", "assistant", day=8),
    ]
    out = insert_legacy_transcript(merged, old, {first.id: first, active.id: active})

    assert [m["id"] for m in out.messages] == [
        "first-question",
        "first-answer",
        "input-a",
        "agent-a",
        "child-message",
        "second-question",
        "second-answer",
        "clock-skew",
        "input-b",
    ]
    links = {link.message_id: link for link in out.message_links}
    assert links["first-answer"].parent_message_id == "first-question"
    assert links["input-a"].parent_message_id == "first-answer"
    assert links["child-message"].parent_message_id == "agent-a"
    assert links["second-question"].parent_message_id == "agent-a"
    assert links["input-b"].parent_message_id == "clock-skew"
    assert out.active_tip_message_id == "input-b"
    assert out.subagents == merged.subagents
    assert out.todos_by_owner == merged.todos_by_owner
    assert out.unreliable_run_ids == {first.id}
    assert merged.message_links[0].parent_message_id is None


def test_timestamp_ties_and_undated_turns_preserve_persisted_order_and_compare_in_utc() -> None:
    dated, undated = _run(2), _run()
    dated.created_at = _at(2).replace(tzinfo=None)
    same_in_seoul = _at(2).astimezone(timezone(timedelta(hours=9)))
    equal_question, equal_link = _row("equal")
    existing = _current(
        [_row("dated-job", run=dated), _row("undated-job", run=undated, parent="dated-job")],
        "undated-job",
    )
    old = [
        _row("no-clock"),
        (equal_question, equal_link.model_copy(update={"created_at": same_in_seoul})),
        _row("tie-reply", "assistant", day=2, parent="equal"),
        _row("another-tie", day=2),
        _row("after", day=3),
    ]
    result = insert_legacy_transcript(existing, old, {dated.id: dated, undated.id: undated})
    assert [m["id"] for m in result.messages] == [
        "no-clock",
        "equal",
        "tie-reply",
        "another-tie",
        "dated-job",
        "after",
        "undated-job",
    ]
    assert result.active_tip_message_id == "undated-job"


@pytest.mark.parametrize(
    "collision", ["wire", "ui", "physical", "public", "checkpoint", "child", "call"]
)
def test_old_id_cannot_alias_an_existing_identity_or_adopt_its_reply(collision: str) -> None:
    run = _run(2)
    public_target = uuid4()
    # Alternate UUID spellings still identify the same protected resource.
    aliases = {
        "wire": "modern",
        "ui": public_target.hex.upper(),
        "physical": str(run.id).upper(),
        "public": "new-run",
        "checkpoint": "checkpoint",
        "child": "child",
        "call": "tool-call",
    }
    selected, link = _row("modern", "assistant", run=run)
    selected["toolCalls"] = [
        {"id": "tool-call", "type": "function", "function": {"name": "task", "arguments": "{}"}}
    ]
    link = link.model_copy(
        update={
            "ui_message_id": str(public_target),
            "run_id": "new-run",
            "checkpoint_id": "checkpoint",
        }
    )
    existing = _current([(selected, link)], "modern")
    existing.subagents = [{"subagentRunId": "child", "name": "helper", "status": "completed"}]
    old = [
        _row(aliases[collision], day=1),
        _row("orphan-reply", "assistant", day=1, parent=aliases[collision]),
        _row("displayable", day=1),
    ]
    restored = insert_legacy_transcript(existing, old, {run.id: run})
    assert [m["id"] for m in restored.messages] == ["displayable", "modern"]
    assert restored.message_links[-1].ui_message_id == str(public_target)
    assert restored.active_tip_message_id == "modern"


def test_legacy_only_rows_are_text_and_have_no_feedback_checkpoint_or_run_handle() -> None:
    question, link = _row("historical", day=1)
    question.update(toolCalls=[{"id": "unverified"}], attachmentUrl="private")
    link = link.model_copy(
        update={
            "ui_message_id": str(uuid4()),
            "run_id": "forged",
            "physical_run_id": str(uuid4()),
            "checkpoint_id": "old",
            "parent_tool_call_id": "forged",
        }
    )
    answer = _row("answer", "assistant", parent="historical")
    false_owner = _row("mismatch")
    false_owner[0]["id"] = "different"
    duplicate = str(uuid4())
    result = insert_legacy_transcript(
        _current([]),
        [(question, link), answer, false_owner, _row(duplicate), _row(UUID(duplicate).hex)],
        {},
    )
    assert result.messages == [
        {"role": "user", "id": "historical", "content": "historical"},
        {"role": "assistant", "id": "answer", "content": "answer"},
    ]
    assert result.active_tip_message_id == "answer"
    assert result.message_links == [
        AgUiSnapshotMessageLink(message_id="historical", created_at=_at(1)),
        AgUiSnapshotMessageLink(
            message_id="answer", parent_message_id="historical", created_at=_at(1)
        ),
    ]


def test_empty_active_run_retains_existing_head_in_mixed_terminal_history() -> None:
    prior, active = _run(2), _run(3, active=True)
    history = _current([_row("existing", "assistant", run=prior)], "existing")
    newer = [_row("imported", day=5)]
    transient = insert_legacy_transcript(history, newer, {prior.id: prior, active.id: active})
    assert [row["id"] for row in transient.messages] == ["imported", "existing"]
    assert transient.active_tip_message_id == "existing"

    completed = insert_legacy_transcript(history, newer, {prior.id: prior})
    assert [row["id"] for row in completed.messages] == ["existing", "imported"]
    assert completed.active_tip_message_id == "imported"
    assert completed.message_links[-1].run_id is None
