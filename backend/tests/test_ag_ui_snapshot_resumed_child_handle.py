"""Keep a managed child's durable handle across physical runs only with its original issuer."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.ag_ui_v1_children import public_subagent_id
from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.services.ag_ui_snapshot_assembly import SnapshotAssembly
from tests.test_ag_ui_snapshot_assembly import PublicReplay, call, checkpoint

ISSUING_RUN = "delegate-turn"
RESUMED_RUN = "turn-after-approval"

type Binding = tuple[str, tuple[str, ...], str]


def _scope(*bindings: Binding) -> ChildVisibility:
    return ChildVisibility(
        allow_legacy_details=False,
        tool_call_ids=frozenset(raw for raw, _, _ in bindings),
        namespaces=frozenset(ns for _, ns, _ in bindings),
        bindings=frozenset(bindings),
    )


@dataclass(frozen=True)
class Child:
    raw: str
    namespace: tuple[str, ...]
    task: str

    @property
    def issuer(self) -> str:
        return f"assistant-{self.raw}"

    @property
    def call_id(self) -> str:
        return public_tool_call_id(ISSUING_RUN, None, self.issuer, self.raw)

    @property
    def binding(self) -> Binding:
        return self.raw, self.namespace, self.task

    def row(self, thread: str, *, completed: bool = False, handle: bool = False) -> dict[str, Any]:
        return {
            "subagentRunId": public_subagent_id(thread, self.namespace),
            "parentToolCallId": self.call_id,
            "parentMessageId": self.issuer,
            "name": "researcher",
            "status": "completed" if completed else "interrupted",
            **({"childTaskId": self.task} if handle else {}),
        }


def _start(
    thread: str,
    physical: UUID,
    *children: Child,
    tool_name: str = "task",
    origin_handle: bool = True,
) -> SnapshotAssembly:
    saved = [
        checkpoint(call(child.issuer, child.raw, name=tool_name), run=physical)
        for child in children
    ]
    assembly = SnapshotAssembly(saved, thread_id=thread)
    replay = PublicReplay(
        visibility=_scope(*(child.binding for child in children)),
        messages=[call(child.issuer, child.call_id, name=tool_name) for child in children],
        root_issuances={(child.issuer, child.raw): child.call_id for child in children},
        subagents=[child.row(thread, handle=origin_handle) for child in children],
    )
    assembly.add_run(physical, ISSUING_RUN, replay)
    return assembly


@pytest.mark.parametrize("echo_handle", [True, False], ids=["echoed", "omitted"])
def test_resumed_child_keeps_the_durable_uuid_and_changes_physical_run(echo_handle: bool) -> None:
    thread = str(uuid4())
    before, after = uuid4(), uuid4()
    left = Child("call-left", ("tools:left", "task:task-a"), str(uuid4()))
    right = Child("call-right", ("tools:right", "task:task-b"), str(uuid4()))
    assembly = _start(thread, before, left, right)
    assembly.add_run(
        after,
        RESUMED_RUN,
        PublicReplay(
            visibility=_scope(left.binding, right.binding),
            subagents=[left.row(thread, completed=True, handle=echo_handle)],
        ),
    )
    rows = {row["subagentRunId"]: row for row in assembly.finish().subagents}

    resumed = rows[public_subagent_id(thread, left.namespace)]
    assert resumed["runId"] == RESUMED_RUN and resumed["physicalRunId"] == str(after)
    assert resumed["status"] == "completed"
    assert resumed["childTaskId"] == left.task
    assert (resumed["parentToolCallId"], resumed["parentMessageId"]) == (left.call_id, left.issuer)
    sibling = rows[public_subagent_id(thread, right.namespace)]
    assert sibling["runId"] == ISSUING_RUN and sibling["physicalRunId"] == str(before)
    assert sibling["childTaskId"] == right.task
    assert (sibling["parentToolCallId"], sibling["parentMessageId"]) == (
        right.call_id,
        right.issuer,
    )


@pytest.mark.parametrize(
    "problem",
    [
        "revoked",
        "wrong-durable-task",
        "wrong-namespace",
        "wrong-raw-call",
        "wrong-parent-call",
        "wrong-parent-message",
        "wrong-lane",
        "ordinary-tool",
        "original-hidden",
        "invented-handle",
    ],
)
def test_resumed_child_cannot_acquire_a_handle_from_a_different_source(problem: str) -> None:
    thread = str(uuid4())
    before, after = uuid4(), uuid4()
    child = Child("call-left", ("tools:left", "task:task-a"), str(uuid4()))
    other_uuid = str(uuid4())
    assembly = _start(
        thread,
        before,
        child,
        origin_handle=problem != "original-hidden",
        tool_name="lookup" if problem == "ordinary-tool" else "task",
    )
    resumed = child.row(thread, handle=True, completed=True)
    visibility = _scope(child.binding)
    if problem == "revoked":
        visibility = _scope()
    elif problem == "wrong-durable-task":
        visibility = _scope((child.raw, child.namespace, other_uuid))
    elif problem == "wrong-namespace":
        visibility = _scope((child.raw, ("tools:another", "task:task-b"), child.task))
    elif problem == "wrong-raw-call":
        visibility = _scope(("other-call", child.namespace, child.task))
    elif problem == "wrong-parent-call":
        resumed["parentToolCallId"] = public_tool_call_id(ISSUING_RUN, None, child.issuer, "other")
    elif problem == "wrong-parent-message":
        resumed["parentMessageId"] = "assistant-other"
    elif problem == "wrong-lane":
        resumed["parentSubagentRunId"] = public_subagent_id(thread, ("task:other",))
    elif problem == "invented-handle":
        resumed["childTaskId"] = other_uuid

    assembly.add_run(after, RESUMED_RUN, PublicReplay(visibility=visibility, subagents=[resumed]))
    result = assembly.finish()
    assert all("childTaskId" not in row for row in result.subagents)


def test_discarded_branch_cannot_move_a_managed_handle_to_its_resumed_physical_run() -> None:
    thread = str(uuid4())
    before, after = uuid4(), uuid4()
    child = Child("call-left", ("tools:left",), str(uuid4()))
    assembly = _start(thread, before, child)
    assembly.add_run(
        after,
        RESUMED_RUN,
        PublicReplay(
            visibility=_scope(child.binding), subagents=[child.row(thread, completed=True)]
        ),
        restrict_to_checkpoint=True,
    )
    snapshot = assembly.finish()
    [record] = snapshot.subagents
    assert record["childTaskId"] == child.task and record["runId"] == ISSUING_RUN
    assert record["physicalRunId"] == str(before)
    assert after in snapshot.unreliable_run_ids
