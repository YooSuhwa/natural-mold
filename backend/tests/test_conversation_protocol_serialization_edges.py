from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass

from langgraph.types import Send
from sqlalchemy import String

from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent, MessageEventChunk
from app.routers.conversation_agent_protocol_checkpoint_state import _serialize_checkpoint_value
from app.routers.conversation_agent_protocol_state import _snapshot_tasks, _snapshot_values


@dataclass(frozen=True, slots=True)
class FakeStateTask:
    id: str
    name: str
    error: str | None
    interrupts: list[object]
    checkpoint: dict[str, str]
    state: Send


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    tasks: tuple[FakeStateTask, ...]


@dataclass(frozen=True, slots=True)
class FakeValueSnapshot:
    values: dict[str, object]


def test_snapshot_tasks_serializes_langgraph_send_state() -> None:
    task = FakeStateTask(
        id="task-1",
        name="agent",
        error=None,
        interrupts=[],
        checkpoint={"checkpoint_id": "cp-1"},
        state=Send("delegate", {"topic": "report"}),
    )

    tasks = _snapshot_tasks(FakeSnapshot(tasks=(task,)))

    json.dumps(tasks)
    assert tasks[0]["state"] == {
        "node": "delegate",
        "arg": {"topic": "report"},
        "timeout": None,
    }


def test_snapshot_tasks_project_internal_offload_references() -> None:
    spill_path = (
        f"/.moldy-offload/{'a' * 32}/{'b' * 32}/{'c' * 32}/large_tool_results/0123456789abcdef_json"
    )
    task = FakeStateTask(
        id="task-offload",
        name="agent",
        error=spill_path,
        interrupts=[],
        checkpoint={"checkpoint_id": "cp-offload"},
        state=Send("delegate", {"result_path": spill_path}),
    )

    tasks = _snapshot_tasks(FakeSnapshot(tasks=(task,)))

    rendered = json.dumps(tasks)
    assert spill_path not in rendered
    assert "/.moldy-offload/" not in rendered
    assert "spill_" not in rendered
    assert "internal_reference_redacted" in rendered


def test_snapshot_tasks_project_paths_and_redact_secrets_without_mutation() -> None:
    secret = "opaque-state-task-secret-42"
    session_name = "session_0123456789abcdef0123456789abcdef.md"
    scope = f"/.moldy-offload/{'a' * 32}/{'b' * 32}/{'c' * 32}"
    history_path = f"{scope}/conversation_history/{session_name}"
    task = FakeStateTask(
        id=f"task-{secret}",
        name=f"agent-{secret}",
        error=f"history={history_path}; credential={secret}",
        interrupts=[
            f"interrupt={secret}",
            {
                "method": "custom:moldy.memory_recalled",
                "params": {
                    "data": {
                        "payload": {
                            "memories": [{"id": "m-task", "content": "private task memory body"}]
                        }
                    }
                },
            },
        ],
        checkpoint={"checkpoint_id": f"cp-{secret}", "history": history_path},
        state=Send(
            "delegate",
            {"credential": secret, "history": history_path},
        ),
    )
    snapshot = FakeSnapshot(tasks=(task,))
    original = deepcopy(snapshot.tasks)

    tasks = _snapshot_tasks(snapshot, secret_values=(secret,))

    rendered = json.dumps(tasks)
    assert secret not in rendered
    assert history_path not in rendered
    assert "/.moldy-offload/" not in rendered
    assert "history_" in rendered
    assert "private task memory body" not in rendered
    assert snapshot.tasks == original


def test_snapshot_values_redact_reserved_memory_event_without_mutation() -> None:
    values: dict[str, object] = {
        "event": {
            "name": "moldy.memory_recalled",
            "payload": {
                "id": "m1",
                "content": "private remembered body",
                "reason": "private reason",
            },
        }
    }
    original = deepcopy(values)

    projected = _snapshot_values(FakeValueSnapshot(values=values))

    assert projected["event"]["payload"]["content"] == "<redacted>"
    assert projected["event"]["payload"]["reason"] == "<redacted>"
    assert values == original


def test_update_state_snapshot_projects_internal_history_reference_without_mutation() -> None:
    session_name = "session_0123456789abcdef0123456789abcdef.md"
    scope = f"/.moldy-offload/{'a' * 32}/{'b' * 32}/{'c' * 32}"
    history_path = f"{scope}/conversation_history/{session_name}"
    values = {
        "_summarization_event": {"file_path": history_path, "cutoff_index": 3},
        "summary": f"Archived at {history_path}",
    }

    projected = _snapshot_values(FakeValueSnapshot(values=values))

    rendered = json.dumps(projected)
    assert history_path not in rendered
    assert "/.moldy-offload/" not in rendered
    assert "file_path" not in rendered
    assert "history_" in rendered
    assert values["_summarization_event"]["file_path"] == history_path


def test_checkpoint_values_serialize_nested_langgraph_send_values() -> None:
    values = {
        "pending": [Send("delegate", {"topic": "report"})],
    }

    serialized = _serialize_checkpoint_value(values)

    json.dumps(serialized)
    assert serialized == {
        "pending": [
            {
                "node": "delegate",
                "arg": {"topic": "report"},
                "timeout": None,
            }
        ],
    }


def test_protocol_event_id_columns_are_wide_enough_for_artifact_event_ids() -> None:
    long_event_id = (
        "83274dae-3d10-4e74-b76d-ec86b993a028:"
        "protocol:00000005:tool-finished:14:call_e2e_hwpx:artifact:0"
    )
    expected_min_length = len(long_event_id)

    column_types = (
        MessageEvent.__table__.c.last_event_id.type,
        MessageEventChunk.__table__.c.first_event_id.type,
        MessageEventChunk.__table__.c.last_event_id.type,
        ConversationRun.__table__.c.last_event_id.type,
    )
    for column_type in column_types:
        assert isinstance(column_type, String)
        assert column_type.length is not None
        assert column_type.length >= expected_min_length
