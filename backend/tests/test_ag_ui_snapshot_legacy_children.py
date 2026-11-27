from dataclasses import replace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.agent_runtime.ag_ui_v1_children import public_subagent_id
from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_legacy_child_binding import LegacyChildBinding
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot
from app.services.ag_ui_snapshot_history import SnapshotHistory, SnapshotMessageRef
from app.services.ag_ui_snapshot_legacy_children import restore_legacy_children


def _fixture() -> tuple[str, SnapshotHistory, AssembledSnapshot, LegacyChildBinding]:
    thread, producer = str(uuid4()), uuid4()
    binding = LegacyChildBinding("reviewer", "call_0", f"tools:{uuid4()}", producer)
    native = AIMessage(
        id="source",
        content="",
        tool_calls=[
            {
                "name": "task",
                "id": "call_0",
                "args": {"subagent_type": "reviewer", "description": "private prompt"},
            }
        ],
    )
    history = SnapshotHistory(
        "leaf",
        (SnapshotMessageRef(native, 0, "cp", None),),
        "checkpoint",
        ("cp", "leaf"),
        checkpoint_run_ids={"cp": producer},
    )
    opaque = public_tool_call_id("run", None, "source", "call_0")
    merged = AssembledSnapshot(
        messages=[
            {
                "id": "source",
                "role": "assistant",
                "toolCalls": [
                    {
                        "id": opaque,
                        "type": "function",
                        "function": {"name": "task", "arguments": "{}"},
                    },
                ],
            }
        ],
        message_links=[
            AgUiSnapshotMessageLink(
                message_id="source",
                checkpoint_id="cp",
                run_id="run",
                physical_run_id=str(producer),
            )
        ],
        subagents=[],
        todos_by_owner={},
        active_tip_message_id="source",
        unreliable_run_ids=set(),
    )
    return thread, history, merged, binding


def test_native_grouped_slots_restore_one_verified_public_child() -> None:
    thread, history, merged, binding = _fixture()
    assert binding.producer_run_id is not None
    ids = {"current_1": binding, "current_2": binding}
    recovered = restore_legacy_children(
        thread,
        history,
        merged,
        ids,
        public_run_id="run",
        physical_run_id=binding.producer_run_id,
        secret_values=("reviewer",),
    )
    ns = (binding.checkpoint_ns,)
    cid = public_subagent_id(thread, ns)
    assert recovered == {ns: cid}
    assert merged.subagents == [
        {
            "subagentRunId": cid,
            "parentMessageId": "source",
            "parentToolCallId": public_tool_call_id("run", None, "source", "call_0"),
            "runId": "run",
            "physicalRunId": str(binding.producer_run_id),
            "name": "<redacted>",
            "status": "interrupted",
        }
    ]
    assert "private prompt" not in str(merged.subagents)


def test_continuation_reuses_namespace_and_issuer_but_current_run_owns_pending() -> None:
    thread, history, merged, binding = _fixture()
    current = uuid4()
    cid = public_subagent_id(thread, (binding.checkpoint_ns,))
    merged.subagents.append(
        {
            "subagentRunId": cid,
            "name": "subagent",
            "status": "completed",
            "result": "stale",
            "runId": "run",
            "physicalRunId": str(binding.producer_run_id),
        }
    )
    assert restore_legacy_children(
        thread,
        history,
        merged,
        {"current": binding},
        public_run_id="continuation",
        physical_run_id=current,
        secret_values=(),
    ) == {(binding.checkpoint_ns,): cid}
    assert len(merged.subagents) == 1
    assert merged.subagents[0] == {
        "subagentRunId": cid,
        "name": "reviewer",
        "status": "interrupted",
        "runId": "continuation",
        "physicalRunId": str(current),
        "parentMessageId": "source",
        "parentToolCallId": public_tool_call_id("run", None, "source", "call_0"),
    }


@pytest.mark.parametrize(
    "case",
    [
        "stale_issuer",
        "unbound_run",
        "wrong_producer",
        "wrong_checkpoint",
        "wrong_name",
        "other_branch",
        "other_tool_id",
        "other_public_run",
        "duplicate_issuer",
        "different_namespaces",
        "conflicting_old_parent",
        "nested_only",
        "managed",
        "idless",
        "retained",
    ],
)
def test_incomplete_or_ambiguous_identity_leaves_children_untouched(case: str) -> None:
    thread, history, merged, binding = _fixture()
    root = binding.producer_run_id
    assert root
    bindings = {"current": binding}
    link = merged.message_links[0]
    match case:
        case "stale_issuer":
            newer = SnapshotMessageRef(AIMessage(id="newer", content="next"), 1, "leaf", "cp")
            history = replace(history, messages=(*history.messages, newer))
        case "unbound_run":
            link.physical_run_id = None
        case "wrong_producer":
            bindings["current"] = replace(binding, producer_run_id=uuid4())
        case "wrong_checkpoint":
            link.checkpoint_id = "other"
        case "wrong_name":
            bindings["current"] = replace(binding, runtime_name="other")
        case "other_branch":
            history = replace(history, other_branch_run_ids=frozenset({root}))
        case "other_tool_id":
            merged.messages[0]["toolCalls"][0]["id"] = "another"
        case "other_public_run":
            link.run_id = "another"
        case "duplicate_issuer":
            merged.messages.append(merged.messages[0])
            merged.message_links.append(link)
        case "different_namespaces":
            bindings["second"] = replace(binding, checkpoint_ns=f"tools:{uuid4()}")
        case "conflicting_old_parent" | "managed":
            row = {
                "subagentRunId": public_subagent_id(thread, (binding.checkpoint_ns,)),
                "runId": "run",
                "physicalRunId": str(root),
            }
            row["parentMessageId" if case == "conflicting_old_parent" else "childTaskId"] = "other"
            merged.subagents.append(row)
        case "nested_only":
            bindings["current"] = replace(
                binding, checkpoint_ns=f"{binding.checkpoint_ns}|tools:other"
            )
        case "idless":
            msg = history.messages[0].message
            assert isinstance(msg, AIMessage)
            msg.id = None
        case "retained":
            history = replace(history, source="retained")
    original = [row.copy() for row in merged.subagents]
    assert (
        restore_legacy_children(
            thread,
            history,
            merged,
            bindings,
            public_run_id="run",
            physical_run_id=root,
            secret_values=(),
        )
        == {}
    )
    assert merged.subagents == original
