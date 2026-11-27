from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_assembly import SnapshotAssembly

type Row = dict[str, Any]


@dataclass
class PublicReplay:
    """A single scoped journal after public frame projection."""

    messages: list[Row] = field(default_factory=list)
    message_links: dict[str, Row] = field(default_factory=dict)
    subagents: list[Row] = field(default_factory=list)
    todos_by_owner: dict[str | None, list[Row]] = field(default_factory=dict)
    legacy_users: list[Row] = field(default_factory=list)


def text_message(mid: str, role: str, text: str) -> Row:
    return {"id": mid, "role": role, "content": text}


def call(mid: str, cid: str, args: str = "{}") -> Row:
    return {
        "id": mid,
        "role": "assistant",
        "toolCalls": [
            {"id": cid, "type": "function", "function": {"name": "task", "arguments": args}}
        ],
    }


def tool(mid: str, cid: str, content: str = "") -> Row:
    return {"id": mid, "role": "tool", "toolCallId": cid, "content": content}


def checkpoint(
    row: Row, *, run: UUID | None = None, parent: str | None = None
) -> tuple[Row, AgUiSnapshotMessageLink]:
    return row, AgUiSnapshotMessageLink(
        message_id=row["id"],
        ui_message_id=str(uuid4()),
        checkpoint_id=f"cp-{row['id']}",
        parent_checkpoint_id=parent,
        sibling_checkpoint_ids=["sibling"] if parent else [],
        physical_run_id=str(run) if run else None,
    )


def test_journal_wire_alias_keeps_checkpoint_feedback_and_branch_identity() -> None:
    physical = uuid4()
    user = checkpoint(text_message("user", "user", "go"))
    saved_call = checkpoint(call("saved-assistant", "call-1"), parent="cp-user")
    saved_tool = checkpoint(tool("saved-tool", "call-1"), parent="cp-saved-assistant")
    answer = checkpoint(text_message("answer", "assistant", "done"))
    assembly = SnapshotAssembly([user, saved_call, saved_tool, answer])
    replay = PublicReplay(
        messages=[
            call("wire-assistant", "call-1", '{"request":"summary"}'),
            tool("wire-tool", "call-1", "public result"),
            answer[0],
        ],
        message_links={
            "wire-tool": {"parentMessageId": "wire-assistant", "parentToolCallId": "call-1"}
        },
    )
    assert assembly.has_root_match(replay, physical)

    assembly.add_run(physical, "public-run", replay)
    result = assembly.finish()

    assert [m["id"] for m in result.messages] == ["user", "wire-assistant", "wire-tool", "answer"]
    assert result.messages[1]["toolCalls"][0]["function"]["arguments"] == '{"request":"summary"}'
    assert result.messages[2]["content"] == "public result"
    links = {link.message_id: link for link in result.message_links}
    for message_id, saved in [("wire-assistant", saved_call), ("wire-tool", saved_tool)]:
        link = links[message_id]
        assert link.run_id == "public-run"
        assert link.physical_run_id == str(physical)
        assert link.ui_message_id == saved[1].ui_message_id
        assert link.checkpoint_id == saved[1].checkpoint_id
        assert link.parent_checkpoint_id == saved[1].parent_checkpoint_id
        assert link.sibling_checkpoint_ids == ["sibling"]
    assert links["wire-tool"].parent_message_id == "wire-assistant"
    assert result.active_tip_message_id == "answer"


def test_child_and_activity_keep_their_turn_and_provenance() -> None:
    physical = uuid4()
    parent = checkpoint(call("assistant-1", "handoff"))
    result_row = checkpoint(tool("result-1", "handoff"))
    next_user = checkpoint(text_message("next-user", "user", "another task"))
    next_reply = checkpoint(text_message("next-reply", "assistant", "another answer"))
    assembly = SnapshotAssembly([parent, result_row, next_user, next_reply])
    child_message = text_message("child-message", "assistant", "child output")
    child_message["subagentRunId"] = "child-1"
    activity = {
        "id": "compaction",
        "role": "activity",
        "activityType": "moldy.compaction",
        "content": {"status": "completed"},
    }
    todo = {"id": "t1", "status": "completed", "content": "inspect"}
    replay = PublicReplay(
        messages=[parent[0], tool("result-1", "handoff", "ok"), child_message, activity],
        message_links={
            "child-message": {
                "subagentRunId": "child-1",
                "parentToolCallId": "handoff",
                "parentMessageId": "assistant-1",
            },
            "compaction": {"parentMessageId": "assistant-1"},
        },
        subagents=[
            {
                "subagentRunId": "child-1",
                "parentToolCallId": "handoff",
                "parentMessageId": "assistant-1",
                "name": "research",
                "status": "completed",
            }
        ],
        todos_by_owner={"child-1": [todo]},
    )
    assembly.add_run(physical, "run-1", replay)
    result = assembly.finish()

    ids = [m["id"] for m in result.messages]
    assert ids == [
        "assistant-1",
        "result-1",
        "child-message",
        "compaction",
        "next-user",
        "next-reply",
    ]
    links = {link.message_id: link for link in result.message_links}
    assert links["child-message"].parent_message_id == "assistant-1"
    assert links["child-message"].parent_tool_call_id == "handoff"
    assert links["child-message"].subagent_run_id == "child-1"
    assert links["child-message"].ui_message_id is None
    assert links["compaction"].physical_run_id == str(physical)
    assert links["compaction"].ui_message_id is None
    assert result.subagents[0]["runId"] == "run-1"
    assert result.subagents[0]["physicalRunId"] == str(physical)
    assert result.todos_by_owner["child-1"] == [todo]
    assert result.active_tip_message_id == "next-reply"


def test_independent_snapshot_requires_each_rows_own_authorized_physical_run() -> None:
    physical, foreign = uuid4(), uuid4()
    user = checkpoint(text_message("user", "user", "agent input"), run=physical)
    reply = checkpoint(text_message("reply", "assistant", "authorized output"))
    other = checkpoint(text_message("other", "assistant", "unrelated output"), run=foreign)
    unknown = checkpoint(text_message("unknown", "user", "unrelated input"))
    assembly = SnapshotAssembly([user, reply, other, unknown], require_run_link=True)
    # The authorized run independently identifies the checkpoint reply.
    assembly.add_run(physical, "job", PublicReplay(messages=[reply[0]]))
    result = assembly.finish()

    assert [m["id"] for m in result.messages] == ["user", "reply"]
    assert [link.physical_run_id for link in result.message_links] == [str(physical)] * 2
    assert result.active_tip_message_id == "reply"


@pytest.mark.parametrize("collision", ["child", "role", "different-run", "ambiguous-tool"])
def test_unrelated_or_ambiguous_identifiers_do_not_prove_branch_membership(collision: str) -> None:
    physical, other = uuid4(), uuid4()
    row = text_message("same", "assistant", "saved")
    candidates = [checkpoint(row, run=other if collision == "different-run" else None)]
    if collision == "child":
        public = {**row, "subagentRunId": "nested"}
    elif collision == "role":
        public = text_message("same", "user", "saved")
    elif collision == "ambiguous-tool":
        candidates = [checkpoint(tool("earlier", "reused")), checkpoint(tool("later", "reused"))]
        public = tool("synthesized", "reused", "ambiguous")
    else:
        public = row
    assembly = SnapshotAssembly(candidates)
    assert not assembly.has_root_match(PublicReplay(messages=[public]), physical)


def test_selected_fork_retains_the_shared_prefix_without_replaying_sibling_suffix() -> None:
    physical = uuid4()
    common = checkpoint(text_message("common", "assistant", "starting"))
    current = checkpoint(text_message("selected", "assistant", "chosen branch"))
    abandoned = text_message("abandoned", "assistant", "sibling branch")
    assembly = SnapshotAssembly([common, current])
    replay = PublicReplay(messages=[common[0], abandoned])
    assert assembly.has_root_match(replay, physical)
    assembly.add_run(physical, "original", replay, restrict_to_checkpoint=True)
    result = assembly.finish()

    assert result.messages == [common[0], current[0]]
    assert result.active_tip_message_id == "selected"
    assert physical in result.unreliable_run_ids


def test_legacy_input_is_only_inserted_into_a_checkpointless_history() -> None:
    physical = uuid4()
    saved_input = checkpoint(text_message("current-user", "user", "selected question"))
    raw_input = text_message("legacy-user", "user", "submitted question")
    reply = text_message("reply", "assistant", "answer")
    replay = PublicReplay(messages=[reply], legacy_users=[raw_input])
    legacy = SnapshotAssembly([], include_legacy_users=True)
    legacy.add_run(physical, "old", replay)
    assert legacy.finish().messages == [raw_input, reply]

    selected = SnapshotAssembly([saved_input])
    selected.add_run(physical, "old", replay)
    assert selected.finish().messages == [saved_input[0], reply]


def test_shared_tool_only_checkpoint_does_not_inherit_text_from_a_later_sibling() -> None:
    physical = uuid4()
    common = checkpoint(call("shared", "task-1"))
    replay = PublicReplay(messages=[{**common[0], "content": "answer from discarded sibling"}])
    assembly = SnapshotAssembly([common])
    assembly.add_run(physical, "run", replay, restrict_to_checkpoint=True)
    result = assembly.finish()

    assert not result.messages[0].get("content")
    assert physical in result.unreliable_run_ids


def test_conflicting_child_cause_on_a_second_run_does_not_attach_to_the_first_child() -> None:
    first, second = uuid4(), uuid4()
    original = checkpoint(call("delegator-one", "first-task"))
    unrelated = checkpoint(call("delegator-two", "second-task"))
    assembly = SnapshotAssembly([original, unrelated])
    for physical, public, saved, child_text in [
        (first, "one", original, "original-child-message"),
        (second, "two", unrelated, "different-child-message"),
    ]:
        parent_id = saved[0]["id"]
        cid = saved[0]["toolCalls"][0]["id"]
        child_message = {
            **text_message(child_text, "assistant", "child answer"),
            "subagentRunId": "same-child-id",
        }
        assembly.add_run(
            physical,
            public,
            PublicReplay(
                messages=[saved[0], child_message],
                message_links={
                    child_text: {
                        "subagentRunId": "same-child-id",
                        "parentMessageId": parent_id,
                        "parentToolCallId": cid,
                    }
                },
                todos_by_owner={
                    "same-child-id": [{"id": "t", "status": "completed", "content": public}]
                },
                subagents=[
                    {
                        "subagentRunId": "same-child-id",
                        "parentToolCallId": cid,
                        "parentMessageId": parent_id,
                        "name": "helper",
                        "status": "completed",
                    }
                ],
            ),
        )
    result = assembly.finish()
    assert [row["parentToolCallId"] for row in result.subagents] == ["first-task"]
    assert "original-child-message" in [row["id"] for row in result.messages]
    assert "different-child-message" not in [row["id"] for row in result.messages]
    assert result.todos_by_owner["same-child-id"][0]["content"] == "one"
    assert second in result.unreliable_run_ids


@pytest.mark.parametrize(
    ("selected", "sibling"),
    [
        (
            text_message("provider-id", "assistant", "answer about the selected question"),
            text_message("provider-id", "assistant", "an unrelated answer"),
        ),
        (call("provider-id", "call-a"), call("provider-id", "call-b")),
    ],
)
def test_reused_provider_id_with_incompatible_output_is_not_a_branch_anchor(
    selected: Row, sibling: Row
) -> None:
    assembly = SnapshotAssembly([checkpoint(selected)])
    assert not assembly.has_root_match(PublicReplay(messages=[sibling]), uuid4())


def test_independent_run_cannot_authenticate_extra_checkpoint_text_with_a_partial_prefix() -> None:
    physical = uuid4()
    unknown = checkpoint(text_message("reused-id", "assistant", "visible part + unowned tail"))
    replay = PublicReplay(messages=[text_message("reused-id", "assistant", "visible part")])
    assembly = SnapshotAssembly([unknown], require_run_link=True)
    assembly.add_run(physical, "independent-job", replay)
    result = assembly.finish()

    assert result.messages == replay.messages
    assert result.message_links[0].physical_run_id == str(physical)
    assert result.message_links[0].ui_message_id is None
    assert result.message_links[0].checkpoint_id is None


def test_scoped_message_link_drops_a_parent_reference_to_a_hidden_independent_turn() -> None:
    physical, foreign = uuid4(), uuid4()
    visible = checkpoint(text_message("known-user", "user", "job request"), run=physical)
    hidden = checkpoint(text_message("hidden-id", "assistant", "other conversation"), run=foreign)
    reply, link = checkpoint(text_message("visible-reply", "assistant", "job result"), run=physical)
    stale_link = link.model_copy(update={"parent_message_id": "hidden-id"})
    assembly = SnapshotAssembly([visible, hidden, (reply, stale_link)], require_run_link=True)
    assembly.add_run(physical, "job", PublicReplay(messages=[reply]))
    result = assembly.finish()

    visible_ids = {row["id"] for row in result.messages}
    assert visible_ids == {"known-user", "visible-reply"}
    assert all(
        link.parent_message_id is None or link.parent_message_id in visible_ids
        for link in result.message_links
    )
