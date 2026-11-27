from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime import ag_ui_v1_issuance as issuance
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_assembly import SnapshotAssembly

type Row = dict[str, Any]


@dataclass
class PublicReplay:
    """A single scoped journal after public frame projection."""

    visibility: ChildVisibility = field(
        default_factory=lambda: ChildVisibility(True, frozenset(), frozenset())
    )
    messages: list[Row] = field(default_factory=list)
    message_links: dict[str, Row] = field(default_factory=dict)
    subagents: list[Row] = field(default_factory=list)
    todos_by_owner: dict[str | None, list[Row]] = field(default_factory=dict)
    legacy_users: list[Row] = field(default_factory=list)
    root_issuances: dict[tuple[str | None, str], str] = field(default_factory=dict)


def text_message(mid: str, role: str, text: str) -> Row:
    return {"id": mid, "role": role, "content": text}


def call(mid: str, cid: str, args: str = "{}", *, name: str = "task") -> Row:
    return {
        "id": mid,
        "role": "assistant",
        "toolCalls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}
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
    saved_call = checkpoint(call("saved-assistant", "call-1"), run=physical, parent="cp-user")
    saved_tool = checkpoint(tool("saved-tool", "call-1"), run=physical, parent="cp-saved-assistant")
    answer = checkpoint(text_message("answer", "assistant", "done"))
    assembly = SnapshotAssembly([user, saved_call, saved_tool, answer])
    parent_id, raw_id = "wire-assistant", "call-1"
    cid = issuance.public_tool_call_id("public-run", None, parent_id, raw_id)
    result_id = issuance.public_tool_result_id(cid)
    replay = PublicReplay(
        messages=[
            call(parent_id, cid, '{"request":"summary"}'),
            tool(result_id, cid, "public result"),
            answer[0],
        ],
        message_links={result_id: {"parentMessageId": parent_id, "parentToolCallId": cid}},
        root_issuances={(parent_id, raw_id): cid},
    )
    assert assembly.has_root_match(replay, physical, public_run_id="public-run")

    assembly.add_run(physical, "public-run", replay)
    result = assembly.finish()

    assert [m["id"] for m in result.messages] == ["user", parent_id, result_id, "answer"]
    assert result.messages[1]["toolCalls"][0]["id"] == cid
    assert result.messages[1]["toolCalls"][0]["function"]["arguments"] == '{"request":"summary"}'
    assert result.messages[2]["content"] == "public result"
    links = {link.message_id: link for link in result.message_links}
    for message_id, saved in [(parent_id, saved_call), (result_id, saved_tool)]:
        link = links[message_id]
        assert link.run_id == "public-run"
        assert link.physical_run_id == str(physical)
        assert link.ui_message_id == saved[1].ui_message_id
        assert link.checkpoint_id == saved[1].checkpoint_id
        assert link.parent_checkpoint_id == saved[1].parent_checkpoint_id
        assert link.sibling_checkpoint_ids == ["sibling"]
    assert links[result_id].parent_message_id == parent_id
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
    cid = issuance.public_tool_call_id("run-1", None, "assistant-1", "handoff")
    result_id = issuance.public_tool_result_id(cid)
    replay = PublicReplay(
        root_issuances={("assistant-1", "handoff"): cid},
        messages=[call("assistant-1", cid), tool(result_id, cid, "ok"), child_message, activity],
        message_links={
            "child-message": {
                "subagentRunId": "child-1",
                "parentToolCallId": cid,
                "parentMessageId": "assistant-1",
            },
            "compaction": {"parentMessageId": "assistant-1"},
        },
        subagents=[
            {
                "subagentRunId": "child-1",
                "parentToolCallId": cid,
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
        result_id,
        "child-message",
        "compaction",
        "next-user",
        "next-reply",
    ]
    links = {link.message_id: link for link in result.message_links}
    assert links["child-message"].parent_message_id == "assistant-1"
    assert links["child-message"].parent_tool_call_id == cid
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
    cid = issuance.public_tool_call_id("run", None, "shared", "task-1")
    replay = PublicReplay(
        messages=[{**call("shared", cid), "content": "answer from discarded sibling"}],
        root_issuances={("shared", "task-1"): cid},
    )
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
        raw = saved[0]["toolCalls"][0]["id"]
        cid = issuance.public_tool_call_id(public, None, parent_id, raw)
        child_message = {
            **text_message(child_text, "assistant", "child answer"),
            "subagentRunId": "same-child-id",
        }
        assembly.add_run(
            physical,
            public,
            PublicReplay(
                messages=[call(parent_id, cid), child_message],
                root_issuances={(parent_id, raw): cid},
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
    assert [row["parentToolCallId"] for row in result.subagents] == [
        issuance.public_tool_call_id("one", None, "delegator-one", "first-task")
    ]
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
    stale_link = link.model_copy(
        update={
            "parent_message_id": "hidden-id",
            "parent_checkpoint_id": hidden[1].checkpoint_id,
            "sibling_checkpoint_ids": [hidden[1].checkpoint_id],
        }
    )
    assembly = SnapshotAssembly([visible, hidden, (reply, stale_link)], require_run_link=True)
    assembly.add_run(physical, "job", PublicReplay(messages=[reply]))
    result = assembly.finish()

    visible_ids = {row["id"] for row in result.messages}
    assert visible_ids == {"known-user", "visible-reply"}
    assert all(
        link.parent_message_id is None or link.parent_message_id in visible_ids
        for link in result.message_links
    )
    reply_link = next(item for item in result.message_links if item.message_id == "visible-reply")
    assert reply_link.checkpoint_id == link.checkpoint_id
    assert reply_link.parent_checkpoint_id != hidden[1].checkpoint_id
    assert hidden[1].checkpoint_id not in reply_link.sibling_checkpoint_ids


def test_idless_output_recovers_wire_alias_only_in_its_checkpoint_stamped_run() -> None:
    physical, elsewhere = uuid4(), uuid4()
    other = checkpoint(text_message("old-synthetic", "assistant", "same"), run=elsewhere)
    here = checkpoint(text_message("current-synthetic", "assistant", "same"), run=physical)
    assembly = SnapshotAssembly(
        [other, here], idless_checkpoint_ids=[other[0]["id"], here[0]["id"]]
    )
    assembly.add_run(
        physical, "current", PublicReplay(messages=[text_message("runtime", "assistant", "same")])
    )
    result = assembly.finish()
    assert [row["id"] for row in result.messages] == ["old-synthetic", "runtime"]
    assert result.message_links[1].ui_message_id == here[1].ui_message_id
    assert result.message_links[1].checkpoint_id == here[1].checkpoint_id
    assert result.active_tip_message_id == "runtime"
    assert not result.unreliable_run_ids


def test_ambiguous_idless_repeat_stays_as_saved_without_double_render_or_cursor() -> None:
    physical = uuid4()
    saved = [
        checkpoint(text_message(f"anon-{i}", "assistant", "OK"), run=physical) for i in range(2)
    ]
    assembly = SnapshotAssembly(saved, idless_checkpoint_ids=[row["id"] for row, _ in saved])
    replay = PublicReplay(messages=[text_message(f"live-{i}", "assistant", "OK") for i in range(2)])
    assembly.add_run(physical, "old", replay)
    result = assembly.finish()
    assert [row["id"] for row in result.messages] == ["anon-0", "anon-1"]
    assert result.unreliable_run_ids == {physical}


def test_reversed_idless_records_cannot_swap_feedback_targets() -> None:
    physical = uuid4()
    saved = [
        checkpoint(text_message(f"cp-{s}", "assistant", s), run=physical) for s in ("one", "two")
    ]
    assembly = SnapshotAssembly(saved, idless_checkpoint_ids=[row["id"] for row, _ in saved])
    replay = PublicReplay(
        messages=[text_message(f"wire-{s}", "assistant", s) for s in ("two", "one")]
    )
    assembly.add_run(physical, "old", replay)
    result = assembly.finish()
    assert [row["id"] for row in result.messages] == ["cp-one", "cp-two"]
    assert result.unreliable_run_ids == {physical}


def test_independent_exact_anchors_distinguish_repeated_idless_text_in_one_run() -> None:
    physical = uuid4()
    saved = [
        checkpoint(text_message("cp-first", "assistant", "OK"), run=physical),
        checkpoint(text_message("barrier", "assistant", "next step"), run=physical),
        checkpoint(text_message("cp-second", "assistant", "OK"), run=physical),
    ]
    assembly = SnapshotAssembly(saved, idless_checkpoint_ids=["cp-first", "cp-second"])
    assembly.add_run(
        physical,
        "old",
        PublicReplay(
            messages=[
                text_message("wire-1", "assistant", "OK"),
                saved[1][0],
                text_message("wire-2", "assistant", "OK"),
            ]
        ),
    )
    result = assembly.finish()
    assert [r["id"] for r in result.messages] == ["wire-1", "barrier", "wire-2"]
    assert [link.ui_message_id for link in result.message_links] == [
        link.ui_message_id for _, link in saved
    ]
    assert not result.unreliable_run_ids


def test_one_run_reuses_raw_id_under_two_assistants_without_swapping_results_or_child_causes() -> (
    None
):
    physical = uuid4()
    raw = "provider-local-task"
    saved = []
    replay = PublicReplay()
    public_results = []
    public_calls = []
    for label in ("alpha", "beta"):
        mid = f"issuer-{label}"
        saved.extend(
            [
                checkpoint(call(mid, raw), run=physical),
                checkpoint(tool(f"saved-result-{label}", raw), run=physical),
            ]
        )
        cid = issuance.public_tool_call_id("public", None, mid, raw)
        rid = issuance.public_tool_result_id(cid)
        public_calls.append(cid)
        public_results.append(rid)
        replay.root_issuances[(mid, raw)] = cid
        replay.messages.extend([call(mid, cid), tool(rid, cid, label)])
        replay.message_links[rid] = {"parentMessageId": mid, "parentToolCallId": cid}
        replay.subagents.append(
            {
                "subagentRunId": label,
                "parentToolCallId": cid,
                "parentMessageId": mid,
                "name": "review",
                "status": "completed",
            }
        )
    assembly = SnapshotAssembly(saved, thread_id="thread")
    assembly.add_run(physical, "public", replay)
    result = assembly.finish()

    assert public_calls[0] != public_calls[1]
    assert [r["id"] for r in result.messages] == [
        "issuer-alpha",
        public_results[0],
        "issuer-beta",
        public_results[1],
    ]
    assert [r["toolCallId"] for r in result.messages if r["role"] == "tool"] == public_calls
    assert [r["content"] for r in result.messages if r["role"] == "tool"] == ["alpha", "beta"]
    assert [link.ui_message_id for link in result.message_links] == [
        link.ui_message_id for _, link in saved
    ]
    assert [(r["subagentRunId"], r["parentToolCallId"]) for r in result.subagents] == list(
        zip(("alpha", "beta"), public_calls, strict=True)
    )


def test_unknown_checkpoint_uses_stable_thread_scoped_non_delegated_tool_pair() -> None:
    raw = "private_local_issuer"
    saved = [
        checkpoint(call("ordinary", raw, name="search")),
        checkpoint(tool("saved-result", raw)),
    ]
    first = SnapshotAssembly(saved, thread_id="first-thread").finish()
    again = SnapshotAssembly(saved, thread_id="first-thread").finish()
    elsewhere = SnapshotAssembly(saved, thread_id="other-thread").finish()

    assert [row["role"] for row in first.messages] == ["assistant", "tool"]
    cid = first.messages[0]["toolCalls"][0]["id"]
    assert cid != raw
    assert first.messages[1]["toolCallId"] == cid
    assert first.messages[1]["id"] == issuance.public_tool_result_id(cid)
    assert first.messages == again.messages
    assert cid != elsewhere.messages[0]["toolCalls"][0]["id"]
    assert raw not in repr(first.messages)
    assert all(link.physical_run_id is None for link in first.message_links)


@pytest.mark.parametrize("explicit", [True, False])
def test_native_parentless_issuance_links_only_to_its_physically_owned_checkpoint(
    explicit: bool,
) -> None:
    physical = uuid4()
    raw = "native-private-call"
    cid = issuance.public_tool_call_id("pub", None, None, raw)
    result_id = issuance.public_tool_result_id(cid)
    saved = [
        checkpoint(call("persisted-issuer", raw, name="search"), run=physical),
        checkpoint(tool("persisted-result", raw), run=physical),
    ]
    replay = PublicReplay(
        messages=[call(cid, cid, name="search"), tool(result_id, cid, "public answer")],
        message_links={result_id: {"parentMessageId": cid, "parentToolCallId": cid}},
        root_issuances={(None, raw): cid} if explicit else {},
    )
    assembly = SnapshotAssembly(saved, thread_id="thread")
    assert assembly.has_root_match(replay, physical, public_run_id="pub") is explicit
    if explicit:
        assembly.add_run(physical, "pub", replay)
        result = assembly.finish()
        assert [r["id"] for r in result.messages] == [cid, result_id]
        assert [link.ui_message_id for link in result.message_links] == [
            link.ui_message_id for _, link in saved
        ]


def test_parentless_issuance_does_not_authorize_a_reused_unowned_checkpoint_call() -> None:
    physical = uuid4()
    raw = "reused-raw"
    cid = issuance.public_tool_call_id("pub", None, None, raw)
    saved = [
        checkpoint(call("ambiguous-a", raw, name="search")),
        checkpoint(tool("result-a", raw)),
        checkpoint(call("ambiguous-b", raw, name="search")),
        checkpoint(tool("result-b", raw)),
    ]
    assembly = SnapshotAssembly(saved, thread_id="thread")
    replay = PublicReplay(
        messages=[
            call(cid, cid, name="search"),
            tool(issuance.public_tool_result_id(cid), cid, "other"),
        ],
        root_issuances={(None, raw): cid},
    )
    assert not assembly.has_root_match(replay, physical, public_run_id="pub")


@pytest.mark.parametrize("bad_cause", ["raw-id", "other-issuer"])
def test_child_control_handle_requires_exact_public_call_and_parent(bad_cause: str) -> None:
    physical, raw = uuid4(), "raw-duplicate"
    first = issuance.public_tool_call_id("run", None, "first", raw)
    second = issuance.public_tool_call_id("run", None, "second", raw)
    wrong = raw if bad_cause == "raw-id" else second
    saved = [checkpoint(call(mid, raw), run=physical) for mid in ("first", "second")]
    replay = PublicReplay(
        root_issuances={("first", raw): first, ("second", raw): second},
        messages=[call("first", first), call("second", second)],
        subagents=[
            {
                "subagentRunId": "verified",
                "parentToolCallId": first,
                "parentMessageId": "first",
                "childTaskId": "valid-task",
                "name": "work",
                "status": "running",
            },
            {
                "subagentRunId": "unverified",
                "parentToolCallId": wrong,
                "parentMessageId": "first",
                "childTaskId": "incorrect-task",
                "name": "work",
                "status": "running",
            },
        ],
    )
    assembly = SnapshotAssembly(saved, thread_id="thread")
    assembly.add_run(physical, "run", replay)
    result = assembly.finish()
    children = {row["subagentRunId"]: row for row in result.subagents}
    assert children["verified"]["childTaskId"] == "valid-task"
    assert "childTaskId" not in children.get("unverified", {})
