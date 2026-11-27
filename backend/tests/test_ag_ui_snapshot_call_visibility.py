from typing import Any

import pytest

from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id, public_tool_result_id
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_call_visibility import (
    canonical_checkpoint_calls,
    filter_checkpoint_calls,
    visible_child_controls,
)

type Row = dict[str, Any]


def call(cid: str, name: str) -> Row:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}


def assistant(mid: str, *calls: Row, text: str = "", lane: str | None = None) -> Row:
    message = {"id": mid, "role": "assistant", "content": text, "toolCalls": list(calls)}
    return {**message, "subagentRunId": lane} if lane else message


def tool(mid: str, cid: str) -> Row:
    return {"id": mid, "role": "tool", "toolCallId": cid, "content": "result"}


def linked(row: Row, run: str | None = "physical") -> tuple[Row, AgUiSnapshotMessageLink]:
    return row, AgUiSnapshotMessageLink(
        message_id=row["id"],
        physical_run_id=run,
        run_id="public" if run else None,
        subagent_run_id=row.get("subagentRunId"),
    )


def access(*calls: str, legacy: bool = False) -> ChildVisibility:
    bindings = frozenset((cid, (f"ns-{cid}",), f"task-{cid}") for cid in calls)
    return ChildVisibility(
        legacy, frozenset(calls), frozenset(ns for _, ns, _ in bindings), bindings
    )


def project(
    entries: list[tuple[Row, AgUiSnapshotMessageLink]],
    *,
    visibility: ChildVisibility | None = None,
    issued: dict[tuple[str | None, str], str] | None = None,
) -> dict[str, Row]:
    return canonical_checkpoint_calls(
        entries, public_run_id="public", visibility=visibility, root_issuances=issued or {}
    )


@pytest.mark.parametrize("grant", [None, access(), access("another")])
def test_revoke_filters_call_id_result_and_empty_placeholder_but_keeps_public_text(
    grant: ChildVisibility | None,
) -> None:
    child = call("child", "task")
    normal = call("clock", "current_datetime")
    question = {"id": "u", "role": "user", "content": "check the time"}
    entries = [
        linked(question),
        linked(assistant("answer", child, normal, text="I will check")),
        linked(tool("normal-result", "clock")),
        linked(tool("private-result", "child")),
        linked(assistant("placeholder", call("second-child", "task"))),
    ]
    result = project(entries, visibility=grant)
    opaque = public_tool_call_id("public", None, "answer", "clock")
    assert result == {
        "u": question,
        "answer": assistant("answer", call(opaque, "current_datetime"), text="I will check"),
        "normal-result": tool(public_tool_result_id(opaque), opaque),
    }
    assert entries[1][0]["toolCalls"] == [child, normal]


@pytest.mark.parametrize("visibility", [access("child"), access(legacy=True)])
def test_current_owner_can_read_checkpoint_only_call_and_result(
    visibility: ChildVisibility,
) -> None:
    issuer = linked(assistant("msg", call("child", "task")))
    result = linked(tool("provider-result-id", "child"))
    converted = project([issuer, result], visibility=visibility)
    opaque = public_tool_call_id("public", None, "msg", "child")
    assert converted == {
        "msg": assistant("msg", call(opaque, "task")),
        "provider-result-id": tool(public_tool_result_id(opaque), opaque),
    }


def test_only_a_verified_same_run_can_alias_a_missing_parent_id() -> None:
    raw = "key"
    registered = public_tool_call_id("public", None, None, raw)
    entry = assistant("archived", call(raw, "clock"))
    expected = public_tool_call_id("public", None, "archived", raw)
    for native in [None, "different-provider-parent"]:
        issued = {(native, raw): registered}
        assert (
            project([linked(entry)], issued=issued)["archived"]["toolCalls"][0]["id"] == registered
        )
        assert (
            project([linked(entry, None)], issued=issued)["archived"]["toolCalls"][0]["id"]
            == expected
        )
    exact: dict[tuple[str | None, str], str] = {("archived", raw): registered}
    assert (
        project([linked(entry, None)], issued=exact)["archived"]["toolCalls"][0]["id"] == registered
    )


def test_reused_provider_ids_use_their_actual_preceding_issuers_and_drop_ambiguous_results() -> (
    None
):
    def entry(mid: str) -> tuple[Row, AgUiSnapshotMessageLink]:
        return linked(assistant(mid, call("repeat", "clock")))

    a = public_tool_call_id("public", None, "a", "repeat")
    b = public_tool_call_id("public", None, "b", "repeat")
    first, second = linked(tool("first", "repeat")), linked(tool("second", "repeat"))
    result = project([entry("a"), first, entry("b"), second])
    assert result["first"] == tool(public_tool_result_id(a), a)
    assert result["second"] == tool(public_tool_result_id(b), b)
    overlap = project(
        [entry("a"), entry("b"), first, second, entry("c"), linked(tool("third", "repeat"))]
    )
    assert set(overlap) == {"a", "b", "c", "third"}
    assert overlap["third"]["toolCallId"] == public_tool_call_id("public", None, "c", "repeat")


def test_an_authenticated_continuation_reuses_its_original_result_identity() -> None:
    original = public_tool_call_id("original", None, "issuer", "reused")
    result = linked(tool("native-result-id", "reused"))
    assert project([result]) == {}
    assert project([result], issued={("issuer", "reused"): original}) == {
        "native-result-id": tool(public_tool_result_id(original), original)
    }
    assert (
        project([result], issued={("issuer", "reused"): original, ("other", "reused"): "conflict"})
        == {}
    )
    current_row = linked(tool(public_tool_result_id(original), original), "resumed")
    preceding = linked(assistant("issuer", call(original, "clock")), "original")
    assert filter_checkpoint_calls([current_row], [preceding]) == {
        current_row[0]["id"]: current_row[0]
    }
    assert filter_checkpoint_calls([current_row], []) == {}


def test_issued_nested_grant_does_not_authorize_reused_provider_id_on_the_root() -> None:
    root, child = ("first",), ("first", "second")
    vis = ChildVisibility(
        False,
        frozenset({"secret"}),
        frozenset({root, child}),
        frozenset({("secret", child, "nested-task")}),
    )
    assert project([linked(assistant("root-msg", call("secret", "task")))], visibility=vis) == {}


def test_final_result_needs_one_visible_issuer_in_the_actual_lane() -> None:
    cid = public_tool_call_id("public", None, "normal", "cid")
    issuer = linked(assistant("nested-parent", call(cid, "task"), lane="nested"))
    result = linked(tool("root-result", cid))
    assert filter_checkpoint_calls([result], [issuer]) == {}
    first = linked(assistant("m", call(cid, "clock")))
    other = linked(assistant("m2", call(cid, "clock")))
    assert filter_checkpoint_calls([result], [first, other]) == {}


@pytest.mark.parametrize(
    ("alteration", "retained"),
    [
        ({}, True),
        ({"parentMessageId": None}, True),
        ({"physicalRunId": "other"}, False),
        ({"runId": "other-public"}, False),
        ({"parentToolCallId": "unrelated"}, False),
        ({"parentSubagentRunId": "root"}, False),
        ({"parentMessageId": "wrong"}, False),
    ],
)
def test_control_requires_a_unique_delegation_in_the_correct_run_and_parent_lane(
    alteration: Row, retained: bool
) -> None:
    parent, link = linked(assistant("m", call("task-call", "task"), lane="nested"))
    child = {
        "subagentRunId": "grandchild",
        "parentSubagentRunId": "nested",
        "parentMessageId": "m",
        "parentToolCallId": "task-call",
        "childTaskId": "current-protected-task",
        "physicalRunId": "physical",
        "runId": "public",
        **alteration,
    }
    [filtered] = visible_child_controls([child], [parent], [link])
    assert ("childTaskId" in filtered) is retained
    assert filtered["subagentRunId"] == "grandchild"
    assert child["childTaskId"] == "current-protected-task"


def test_duplicate_or_non_delegation_cause_cannot_enable_controls() -> None:
    child = {
        "subagentRunId": "actor",
        "parentMessageId": "m",
        "parentToolCallId": "same",
        "physicalRunId": "physical",
        "childTaskId": "sensitive",
    }
    for calls in (
        [linked(assistant("m", call("same", "normal")))],
        [
            linked(assistant("m", call("same", "task"))),
            linked(assistant("m2", call("same", "task"))),
        ],
    ):
        [filtered] = visible_child_controls(
            [child], [msg for msg, _ in calls], [link for _, link in calls]
        )
        assert "childTaskId" not in filtered


def test_repeated_model_call_copy_is_folded_and_conflicting_tool_names_are_omitted() -> None:
    copy = call("key", "clock")
    ans = assistant("msg", copy, copy)
    response = tool("reply", "key")
    normal = project([linked(ans), linked(response)])
    assert len(normal["msg"]["toolCalls"]) == 1
    assert normal["reply"]["toolCallId"] == normal["msg"]["toolCalls"][0]["id"]
    conflict = assistant("msg", copy, call("key", "task"), text="Checking.")
    assert project([linked(conflict), linked(response)], visibility=access(legacy=True)) == {
        "msg": {"id": "msg", "role": "assistant", "content": "Checking."}
    }
