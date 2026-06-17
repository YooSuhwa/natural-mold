from __future__ import annotations

import json
from dataclasses import dataclass

from langgraph.types import Send

from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent, MessageEventChunk
from app.routers.conversation_agent_protocol_checkpoint_state import _serialize_checkpoint_value
from app.routers.conversation_agent_protocol_state import _snapshot_tasks


@dataclass(frozen=True, slots=True)
class FakeStateTask:
    id: str
    name: str
    error: str | None
    interrupts: list[str]
    checkpoint: dict[str, str]
    state: Send


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    tasks: tuple[FakeStateTask, ...]


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

    assert MessageEvent.__table__.c.last_event_id.type.length >= expected_min_length
    assert MessageEventChunk.__table__.c.first_event_id.type.length >= expected_min_length
    assert MessageEventChunk.__table__.c.last_event_id.type.length >= expected_min_length
    assert ConversationRun.__table__.c.last_event_id.type.length >= expected_min_length
