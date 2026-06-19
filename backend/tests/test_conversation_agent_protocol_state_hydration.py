from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.services.thread_branch_service import _CheckpointSlim
from tests.conftest import TEST_USER_ID


class _FakeCheckpointer:
    def __init__(
        self,
        checkpoints: list[_CheckpointSlim],
        *,
        values_by_checkpoint: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._values_by_checkpoint = values_by_checkpoint or {}

    async def alist(self, _config: Any) -> AsyncIterator[Any]:
        for checkpoint in self._checkpoints:
            channel_values = dict(self._values_by_checkpoint.get(checkpoint.checkpoint_id, {}))
            channel_values.setdefault("messages", checkpoint.messages)
            yield type(
                "CheckpointTuple",
                (),
                {
                    "config": {"configurable": {"checkpoint_id": checkpoint.checkpoint_id}},
                    "parent_config": (
                        {"configurable": {"checkpoint_id": checkpoint.parent_checkpoint_id}}
                        if checkpoint.parent_checkpoint_id
                        else None
                    ),
                    "checkpoint": {"channel_values": channel_values},
                },
            )()


async def _seed_protocol_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="protocol-state@test.dev", name="Protocol User")
        db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="Protocol State Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    conversation = Conversation(agent_id=agent.id, title="Protocol State Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


def _approval_payload() -> dict[str, object]:
    return {
        "action_requests": [{"name": "send_email", "args": {"to": "user@example.com"}}],
        "review_configs": [
            {"action_name": "send_email", "allowed_decisions": ["approve", "reject"]}
        ],
    }


@pytest.mark.asyncio
async def test_thread_state_hydrates_pending_input_requested_interrupt(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    run_id = uuid.uuid4()
    payload = _approval_payload()
    db.add(
        ConversationRun(
            id=run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="interrupted",
            is_active=False,
            interrupt_id="intr-1",
        )
    )
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run_id),
            events=[
                stored_protocol_event(
                    run_id=str(run_id),
                    thread_id=str(conversation.id),
                    seq=4,
                    method="input.requested",
                    namespace=["tools:call-1"],
                    data={"interrupt_id": "intr-1", "payload": payload},
                    event_id="input-evt-1",
                )
            ],
            last_event_id="input-evt-1",
            status="completed",
        )
    )
    await db.commit()

    state_response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )
    compat_response = await client.get(f"/api/conversations/{conversation.id}/langgraph/state")

    assert state_response.status_code == 200
    interrupt = {"id": "intr-1", "value": payload, "ns": ["tools:call-1"]}
    assert state_response.json()["tasks"] == [
        {"id": str(run_id), "name": "interrupted", "interrupts": [interrupt]}
    ]

    assert compat_response.status_code == 200
    assert compat_response.json()["interrupts"] == [interrupt]


@pytest.mark.asyncio
async def test_thread_state_hydrates_pending_tasks_interrupts(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    run_id = uuid.uuid4()
    payload = _approval_payload()
    db.add(
        ConversationRun(
            id=run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="interrupted",
            is_active=False,
            interrupt_id="intr-task",
        )
    )
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run_id),
            events=[
                stored_protocol_event(
                    run_id=str(run_id),
                    thread_id=str(conversation.id),
                    seq=5,
                    method="tasks",
                    namespace=["agent"],
                    data={
                        "id": "task-1",
                        "name": "tools",
                        "interrupts": [
                            {"id": "intr-task", "value": payload, "ns": ["tools:call-1"]}
                        ],
                    },
                )
            ],
            status="completed",
        )
    )
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    assert response.json()["tasks"] == [
        {
            "id": str(run_id),
            "name": "interrupted",
            "interrupts": [{"id": "intr-task", "value": payload, "ns": ["tools:call-1"]}],
        }
    ]


@pytest.mark.asyncio
async def test_thread_state_omits_interrupt_after_resume_child_exists(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent_run_id = uuid.uuid4()
    parent_run = ConversationRun(
        id=parent_run_id,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="interrupted",
        is_active=False,
        interrupt_id="intr-1",
    )
    db.add(parent_run)
    db.add(
        ConversationRun(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            parent_run_id=parent_run.id,
            source="resume",
            status="completed",
            is_active=False,
            interrupt_id="intr-1",
        )
    )
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(parent_run_id),
            events=[
                stored_protocol_event(
                    run_id=str(parent_run_id),
                    thread_id=str(conversation.id),
                    seq=4,
                    method="input.requested",
                    data={"interrupt_id": "intr-1", "payload": _approval_payload()},
                )
            ],
            status="completed",
        )
    )
    await db.commit()

    state_response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )
    compat_response = await client.get(f"/api/conversations/{conversation.id}/langgraph/state")

    assert state_response.status_code == 200
    assert state_response.json()["tasks"] == []
    assert compat_response.status_code == 200
    assert compat_response.json()["interrupts"] == []


@pytest.mark.asyncio
async def test_thread_state_exposes_checkpoint_mapping_for_messages(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    root = _CheckpointSlim(
        checkpoint_id="ck-root",
        parent_checkpoint_id=None,
        messages=[],
    )
    parent = _CheckpointSlim(
        checkpoint_id="ck-user",
        parent_checkpoint_id="ck-root",
        messages=[HumanMessage(id="user-raw-1", content="hello")],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="ck-assistant",
        parent_checkpoint_id="ck-user",
        messages=[
            HumanMessage(id="user-raw-1", content="hello"),
            AIMessage(id="assistant-raw-1", content="hi"),
        ],
    )
    late_streaming_checkpoint = _CheckpointSlim(
        checkpoint_id="ck-assistant-late",
        parent_checkpoint_id="ck-assistant",
        messages=[
            HumanMessage(id="user-raw-1", content="hello"),
            AIMessage(id="assistant-raw-1", content="hi"),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([late_streaming_checkpoint, leaf, parent, root]),
    )

    state_response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )
    compat_response = await client.get(f"/api/conversations/{conversation.id}/langgraph/state")

    assert state_response.status_code == 200
    state = state_response.json()
    user_ui_id = str(parse_msg_id("user-raw-1", conversation.id, 0))
    assistant_ui_id = str(parse_msg_id("assistant-raw-1", conversation.id, 1))
    assert state["metadata"]["checkpoint_by_message_id"] == {
        "user-raw-1": "ck-user",
        user_ui_id: "ck-user",
        "assistant-raw-1": "ck-assistant",
        assistant_ui_id: "ck-assistant",
    }
    assert state["metadata"]["parent_checkpoint_by_message_id"] == {
        "user-raw-1": "ck-root",
        user_ui_id: "ck-root",
        "assistant-raw-1": "ck-user",
        assistant_ui_id: "ck-user",
    }
    messages = state["values"]["messages"]
    assert messages[0]["additional_kwargs"]["metadata"]["checkpoint_id"] == "ck-user"
    assert messages[1]["additional_kwargs"]["metadata"]["checkpoint_id"] == "ck-assistant"

    assert compat_response.status_code == 200
    assert compat_response.json()["checkpoint_by_message_id"] == {
        "user-raw-1": "ck-user",
        user_ui_id: "ck-user",
        "assistant-raw-1": "ck-assistant",
        assistant_ui_id: "ck-assistant",
    }
    assert compat_response.json()["parent_checkpoint_by_message_id"] == {
        "user-raw-1": "ck-root",
        user_ui_id: "ck-root",
        "assistant-raw-1": "ck-user",
        assistant_ui_id: "ck-user",
    }


@pytest.mark.asyncio
async def test_thread_state_assigns_stable_ids_to_idless_langchain_messages(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    root = _CheckpointSlim(
        checkpoint_id="ck-root",
        parent_checkpoint_id=None,
        messages=[],
    )
    parent = _CheckpointSlim(
        checkpoint_id="ck-user",
        parent_checkpoint_id="ck-root",
        messages=[HumanMessage(content="hello")],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="ck-assistant",
        parent_checkpoint_id="ck-user",
        messages=[HumanMessage(content="hello"), AIMessage(content="hi")],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([leaf, parent, root]),
    )

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    state = response.json()
    user_ui_id = str(parse_msg_id(None, conversation.id, 0))
    assistant_ui_id = str(parse_msg_id(None, conversation.id, 1))
    assert state["values"]["messages"][0]["id"] == user_ui_id
    assert state["values"]["messages"][1]["id"] == assistant_ui_id
    assert state["metadata"]["checkpoint_by_message_id"][user_ui_id] == "ck-user"
    assert state["metadata"]["parent_checkpoint_by_message_id"][user_ui_id] == "ck-root"


@pytest.mark.asyncio
async def test_thread_state_exposes_branch_metadata_for_langchain_messages(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    root = _CheckpointSlim(
        checkpoint_id="ck-root",
        parent_checkpoint_id=None,
        messages=[],
    )
    parent = _CheckpointSlim(
        checkpoint_id="ck-user",
        parent_checkpoint_id="ck-root",
        messages=[HumanMessage(id="user-branch-1", content="hello")],
    )
    old_leaf = _CheckpointSlim(
        checkpoint_id="ck-assistant-1-old",
        parent_checkpoint_id="ck-user",
        messages=[
            HumanMessage(id="user-branch-1", content="hello"),
            AIMessage(id="assistant-old", content="old"),
        ],
    )
    active_leaf = _CheckpointSlim(
        checkpoint_id="ck-assistant-2-new",
        parent_checkpoint_id="ck-user",
        messages=[
            HumanMessage(id="user-branch-1", content="hello"),
            AIMessage(id="assistant-new", content="new"),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([active_leaf, old_leaf, parent, root]),
    )

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    messages = response.json()["values"]["messages"]
    assistant_metadata = messages[1]["additional_kwargs"]["metadata"]
    assert assistant_metadata["branches"] == [
        str(parse_msg_id("assistant-old", conversation.id, 1)),
        str(parse_msg_id("assistant-new", conversation.id, 1)),
    ]
    assert assistant_metadata["siblingCheckpointIds"] == [
        "ck-assistant-1-old",
        "ck-assistant-2-new",
    ]
    assert assistant_metadata["activeBranchId"] == str(
        parse_msg_id("assistant-new", conversation.id, 1)
    )
    assert assistant_metadata["branchCheckpointId"] == "ck-assistant-2-new"
    assert assistant_metadata["branchIndex"] == 1
    assert assistant_metadata["branchTotal"] == 2


@pytest.mark.asyncio
async def test_thread_state_uses_active_checkpoint_for_same_id_user_edit_branches(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    old_leaf = _CheckpointSlim(
        checkpoint_id="ck-z-old",
        parent_checkpoint_id=None,
        messages=[
            HumanMessage(id="user-same-id", content="안녕?"),
            AIMessage(id="assistant-old", content="old"),
        ],
    )
    middle_leaf = _CheckpointSlim(
        checkpoint_id="ck-a-middle",
        parent_checkpoint_id=None,
        messages=[
            HumanMessage(id="user-same-id", content="바보"),
            AIMessage(id="assistant-middle", content="middle"),
        ],
    )
    active_leaf = _CheckpointSlim(
        checkpoint_id="ck-m-new",
        parent_checkpoint_id=None,
        messages=[
            HumanMessage(id="user-same-id", content="반가워"),
            AIMessage(id="assistant-new", content="new"),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([active_leaf, middle_leaf, old_leaf]),
    )

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    state = response.json()
    messages = state["values"]["messages"]
    user_metadata = messages[0]["additional_kwargs"]["metadata"]
    assert messages[0]["content"] == "반가워"
    assert user_metadata["checkpoint_id"] == "ck-m-new"
    assert user_metadata["siblingCheckpointIds"] == ["ck-z-old", "ck-a-middle", "ck-m-new"]
    assert user_metadata["branchIndex"] == 2
    assert user_metadata["branchTotal"] == 3
    assert "branches" not in messages[1]["additional_kwargs"]["metadata"]


@pytest.mark.asyncio
async def test_thread_state_uses_unique_branch_ids_for_synthetic_langchain_messages(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    root = _CheckpointSlim(
        checkpoint_id="ck-root",
        parent_checkpoint_id=None,
        messages=[],
    )
    parent = _CheckpointSlim(
        checkpoint_id="ck-user",
        parent_checkpoint_id="ck-root",
        messages=[HumanMessage(content="hello")],
    )
    old_leaf = _CheckpointSlim(
        checkpoint_id="ck-assistant-1-old",
        parent_checkpoint_id="ck-user",
        messages=[HumanMessage(content="hello"), AIMessage(content="old")],
    )
    active_leaf = _CheckpointSlim(
        checkpoint_id="ck-assistant-2-new",
        parent_checkpoint_id="ck-user",
        messages=[HumanMessage(content="hello"), AIMessage(content="new")],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([active_leaf, old_leaf, parent, root]),
    )

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    messages = response.json()["values"]["messages"]
    assistant_metadata = messages[1]["additional_kwargs"]["metadata"]
    assert assistant_metadata["branches"] == [
        str(parse_msg_id("synthetic-1:ck-assistant-1-old", conversation.id, 1)),
        str(parse_msg_id("synthetic-1:ck-assistant-2-new", conversation.id, 1)),
    ]
    assert len(set(assistant_metadata["branches"])) == 2
    assert assistant_metadata["activeBranchId"] == str(
        parse_msg_id("synthetic-1:ck-assistant-2-new", conversation.id, 1)
    )
    assert assistant_metadata["branchIndex"] == 1
    assert assistant_metadata["branchTotal"] == 2


@pytest.mark.asyncio
async def test_thread_state_redacts_sensitive_tool_args_without_losing_usage_metrics(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    leaf = _CheckpointSlim(
        checkpoint_id="ck-secret",
        parent_checkpoint_id=None,
        messages=[
            HumanMessage(id="user-secret-1", content="hello"),
            AIMessage(
                id="assistant-secret-1",
                content="done",
                tool_calls=[
                    {
                        "id": "call-secret",
                        "name": "execute_in_skill",
                        "args": {"api_key": "SECRET_VALUE", "query": "safe"},
                    }
                ],
                usage_metadata={"input_tokens": 30, "output_tokens": 12, "total_tokens": 42},
            ),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([leaf]),
    )

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    body = response.json()
    assert "SECRET_VALUE" not in repr(body)
    assistant = body["values"]["messages"][1]
    assert assistant["tool_calls"][0]["args"] == {"api_key": "<redacted>", "query": "safe"}
    assert assistant["usage_metadata"] == {
        "input_tokens": 30,
        "output_tokens": 12,
        "total_tokens": 42,
    }


@pytest.mark.asyncio
async def test_thread_state_marks_running_run_as_active_for_protocol_hydration(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    run_id = uuid.uuid4()
    db.add(
        ConversationRun(
            id=run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="running",
            is_active=True,
        )
    )
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    state = response.json()
    assert state["next"] == ["__moldy_active_run__"]
    assert state["metadata"]["active_run"] == {"id": str(run_id), "status": "running"}
    assert state["metadata"]["latest_run"] == {"id": str(run_id), "status": "running"}
    assert state["values"]["__moldy_runtime"]["active_run"] == {
        "id": str(run_id),
        "status": "running",
    }
    assert state["values"]["__moldy_runtime"]["latest_run"] == {
        "id": str(run_id),
        "status": "running",
    }


@pytest.mark.asyncio
async def test_thread_state_marks_old_active_run_stale_for_protocol_hydration(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_stale.settings.chat_run_stale_after_seconds",
        1,
    )
    conversation = await _seed_protocol_conversation(db)
    run_id = uuid.uuid4()
    old = utc_now_naive() - timedelta(minutes=10)
    db.add(
        ConversationRun(
            id=run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="running",
            is_active=True,
            heartbeat_at=old,
            started_at=old,
            created_at=old,
        )
    )
    await db.commit()

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    state = response.json()
    assert state["next"] == []
    assert "active_run" not in state["metadata"]
    assert state["metadata"]["latest_run"] == {"id": str(run_id), "status": "stale"}
    assert state["values"]["__moldy_runtime"]["latest_run"] == {
        "id": str(run_id),
        "status": "stale",
    }

    refreshed = await db.get(ConversationRun, run_id)
    assert refreshed is not None
    assert refreshed.status == "stale"
    assert refreshed.is_active is False


@pytest.mark.asyncio
async def test_thread_state_preserves_deepagents_checkpoint_values(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    leaf = _CheckpointSlim(
        checkpoint_id="ck-state",
        parent_checkpoint_id=None,
        messages=[HumanMessage(id="user-state-1", content="hello")],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer(
            [leaf],
            values_by_checkpoint={
                "ck-state": {
                    "todos": [{"id": "todo-1", "content": "Plan", "status": "in_progress"}],
                    "files": {"notes.md": {"content": "# Notes"}},
                    "async_tasks": {"task-1": {"status": "running"}},
                }
            },
        ),
    )

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 200
    values = response.json()["values"]
    assert values["messages"][0]["id"] == "user-state-1"
    assert values["todos"] == [{"id": "todo-1", "content": "Plan", "status": "in_progress"}]
    assert values["files"] == {"notes.md": {"content": "# Notes"}}
    assert values["async_tasks"] == {"task-1": {"status": "running"}}


@pytest.mark.asyncio
async def test_thread_history_returns_checkpoint_snapshots_instead_of_current_state_copies(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent = _CheckpointSlim(
        checkpoint_id="ck-user",
        parent_checkpoint_id=None,
        messages=[HumanMessage(id="user-history-1", content="hello")],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="ck-assistant",
        parent_checkpoint_id="ck-user",
        messages=[
            HumanMessage(id="user-history-1", content="hello"),
            AIMessage(id="assistant-history-1", content="hi"),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state.get_checkpointer",
        lambda: _FakeCheckpointer([leaf, parent]),
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/history",
        json={"limit": 2},
    )

    assert response.status_code == 200
    history = response.json()
    assert [state["checkpoint"]["checkpoint_id"] for state in history] == [
        "ck-assistant",
        "ck-user",
    ]
    assert [len(state["values"]["messages"]) for state in history] == [2, 1]
