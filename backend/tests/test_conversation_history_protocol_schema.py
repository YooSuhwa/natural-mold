from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.routers import conversation_agent_protocol_contracts as router_contracts
from app.schemas.conversation_history_protocol import (
    HistoryRequest,
    RunMetadata,
    ThreadCheckpoint,
    ThreadHistoryCursor,
    ThreadStateResponseInput,
    build_state_response,
)
from app.schemas.conversation_refs import ConversationRef


def test_router_contract_reexports_history_schema_types() -> None:
    assert router_contracts.HistoryRequest is HistoryRequest
    assert router_contracts.ThreadCheckpoint is ThreadCheckpoint
    assert router_contracts.ThreadHistoryCursor is ThreadHistoryCursor


def test_history_request_preserves_defaults_nullability_and_bounds() -> None:
    request = HistoryRequest()

    assert request.model_dump() == {
        "limit": 10,
        "before": None,
        "metadata": None,
        "checkpoint": None,
    }
    with pytest.raises(ValidationError):
        HistoryRequest(limit=0)
    with pytest.raises(ValidationError):
        request.__setattr__("limit", 20)


def test_history_cursor_preserves_extra_fields_and_nested_checkpoint() -> None:
    cursor = ThreadHistoryCursor.model_validate(
        {
            "checkpoint_id": None,
            "checkpoint_ns": None,
            "configurable": {"thread_id": None, "checkpoint_id": None, "checkpoint_ns": None},
            "future_cursor_field": "preserved",
        }
    )

    assert cursor.model_dump() == {
        "checkpoint_id": None,
        "checkpoint_ns": None,
        "configurable": {
            "thread_id": None,
            "checkpoint_id": None,
            "checkpoint_ns": None,
        },
        "future_cursor_field": "preserved",
    }


def test_state_response_wrapper_matches_typed_builder_wire_output() -> None:
    conversation = ConversationRef(
        id=uuid.UUID("10000000-0000-0000-0000-000000000001"),
        agent_id=uuid.UUID("20000000-0000-0000-0000-000000000002"),
        active_branch_checkpoint_id="active-checkpoint",
        queue_paused=False,
        updated_at=datetime(2026, 10, 12, 3, 4, 5, tzinfo=UTC),
    )
    values = {"messages": [{"type": "human", "content": "hello"}], "count": 1}
    tasks = [{"id": "task-1", "name": "agent"}]
    active_run = RunMetadata(id="run-1", status="running")
    latest_run = RunMetadata(id="run-0", status="completed")
    parent = {"checkpoint_id": "parent-checkpoint"}

    wrapper = router_contracts.state_response(
        conversation,
        values=values,
        next_nodes=["tools"],
        tasks=tasks,
        checkpoint_id="checkpoint-1",
        checkpoint_ns="namespace",
        checkpoint_by_message_id={"message-1": "checkpoint-1"},
        parent_checkpoint_by_message_id={"message-1": "parent-checkpoint"},
        active_run=active_run,
        latest_run=latest_run,
        metadata_source="checkpoint",
        created_at="2026-10-12T03:04:05Z",
        parent_checkpoint=parent,
    )
    lower = build_state_response(
        conversation,
        ThreadStateResponseInput(
            values=values,
            next_nodes=["tools"],
            tasks=tasks,
            checkpoint_id="checkpoint-1",
            checkpoint_ns="namespace",
            checkpoint_by_message_id={"message-1": "checkpoint-1"},
            parent_checkpoint_by_message_id={"message-1": "parent-checkpoint"},
            active_run=active_run,
            latest_run=latest_run,
            metadata_source="checkpoint",
            created_at="2026-10-12T03:04:05Z",
            parent_checkpoint=parent,
        ),
    )

    assert (
        wrapper
        == lower
        == {
            "values": {
                "messages": [{"type": "human", "content": "hello"}],
                "count": 1,
                "__moldy_runtime": {"active_run": active_run, "latest_run": latest_run},
            },
            "next": ["tools"],
            "tasks": tasks,
            "checkpoint": {
                "thread_id": str(conversation.id),
                "checkpoint_id": "checkpoint-1",
                "checkpoint_ns": "namespace",
            },
            "metadata": {
                "conversation_id": str(conversation.id),
                "agent_id": str(conversation.agent_id),
                "source": "checkpoint",
                "checkpoint_by_message_id": {"message-1": "checkpoint-1"},
                "parent_checkpoint_by_message_id": {"message-1": "parent-checkpoint"},
                "active_run": active_run,
                "latest_run": latest_run,
            },
            "created_at": "2026-10-12T03:04:05Z",
            "parent_checkpoint": parent,
        }
    )
