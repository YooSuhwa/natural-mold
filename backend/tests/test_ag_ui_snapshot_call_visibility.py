from typing import Any

import pytest

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_call_visibility import (
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
    return ChildVisibility(legacy, frozenset(calls), frozenset())


@pytest.mark.parametrize("grants", [{}, {"physical": access()}, {"other": access("child")}])
def test_revoke_filters_call_id_result_and_empty_placeholder_but_keeps_public_text(
    grants: dict[str, ChildVisibility],
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
    result = filter_checkpoint_calls(entries, [], grants)
    assert result == {
        "u": question,
        "answer": assistant("answer", normal, text="I will check"),
        "normal-result": tool("normal-result", "clock"),
    }
    assert entries[1][0]["toolCalls"] == [child, normal]


@pytest.mark.parametrize("visibility", [access("child"), access(legacy=True)])
def test_current_owner_can_read_checkpoint_only_call_and_result(
    visibility: ChildVisibility,
) -> None:
    issuer = linked(assistant("msg", call("child", "task")))
    result = linked(tool("result", "child"))
    assert filter_checkpoint_calls([issuer, result], [], {"physical": visibility}) == {
        "msg": issuer[0],
        "result": result[0],
    }
    assert filter_checkpoint_calls([result], [issuer], {"physical": visibility}) == {
        "result": result[0]
    }


def test_result_cannot_inherit_another_runs_cause_or_an_ambiguous_issuer() -> None:
    unknown = linked(tool("no-owner", "a"), None)
    foreign = linked(assistant("foreign", call("a", "clock")), "other")
    ambiguous = linked(assistant("one", call("b", "clock"), call("b", "clock")))
    denied = linked(tool("reused", "b"))
    assert (
        filter_checkpoint_calls(
            [unknown, denied], [foreign, ambiguous], {"physical": access(legacy=True)}
        )
        == {}
    )
    previous = linked(assistant("old", call("a", "clock")), None)
    assert filter_checkpoint_calls([unknown], [foreign, previous], {}) == {}
    assert filter_checkpoint_calls([unknown], [previous], {}) == {"no-owner": unknown[0]}


def test_result_cannot_inherit_a_child_lanes_issuer() -> None:
    issuer = linked(assistant("nested-parent", call("shared-call", "task"), lane="nested"))
    result = linked(tool("root-result", "shared-call"))
    assert filter_checkpoint_calls([result], [issuer], {"physical": access("shared-call")}) == {}


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
