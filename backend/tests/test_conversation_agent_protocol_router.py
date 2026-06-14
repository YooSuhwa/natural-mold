from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import BrokeredEvent
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.runtime_config import AgentConfig
from app.main import create_app
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.routers.conversation_agent_protocol_runtime import protocol_events_from_broker
from app.services import trace_storage
from tests.conftest import TEST_USER_ID


class _FakeSnapshot:
    def __init__(self, *, values: dict[str, Any], checkpoint_id: str | None) -> None:
        self.values = values
        self.config = {"configurable": {"checkpoint_id": checkpoint_id}}
        self.next: tuple[str, ...] = ()
        self.tasks: tuple[dict[str, Any], ...] = ()
        self.metadata: dict[str, Any] = {}
        self.created_at: str | None = None


class _FakeStateGraph:
    def __init__(self) -> None:
        self.snapshot = _FakeSnapshot(values={}, checkpoint_id=None)
        self.updates: list[tuple[dict[str, Any], dict[str, Any], str | None, str | None]] = []

    async def aget_state(self, _config: dict[str, Any]) -> _FakeSnapshot:
        return self.snapshot

    async def aupdate_state(
        self,
        config: dict[str, Any],
        values: dict[str, Any],
        *,
        as_node: str | None,
        task_id: str | None = None,
    ) -> None:
        self.updates.append((config, values, as_node, task_id))
        self.snapshot = _FakeSnapshot(values=values, checkpoint_id="ck-updated")


async def _seed_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
) -> Conversation:
    user = await db.get(User, user_id)
    if user is None:
        user = User(id=user_id, email=f"{user_id.hex[:8]}@test.dev", name="Test")
        db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="Protocol Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    conversation = Conversation(agent_id=agent.id, title="Protocol Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


def test_agent_protocol_routes_registered_from_conversation_facade() -> None:
    from fastapi.routing import APIRoute

    expected = {
        ("POST", "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/commands"),
        ("GET", "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/state"),
        ("POST", "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/state"),
        ("POST", "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/history"),
        (
            "POST",
            "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/stream/events",
        ),
        ("GET", "/api/conversations/{conversation_id}/langgraph/state"),
    }
    actual = {
        (method, route.path)
        for route in create_app().routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert expected <= actual


def test_agent_protocol_stream_events_route_requires_csrf() -> None:
    from fastapi.routing import APIRoute

    from app.dependencies import verify_csrf

    route = next(
        route
        for route in create_app().routes
        if isinstance(route, APIRoute)
        and route.path
        == "/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/stream/events"
    )

    assert any(dependency.call is verify_csrf for dependency in route.dependant.dependencies)


@pytest.mark.asyncio
async def test_thread_state_requires_authentication(raw_client: AsyncClient) -> None:
    conversation_id = uuid.uuid4()

    response = await raw_client.get(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/state"
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_thread_state_hides_conversations_owned_by_another_user(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db, user_id=uuid.uuid4())

    response = await client.get(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_run_start_command_defaults_multitask_strategy_to_reject(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_conversation(db)
    started: dict[str, Any] = {}

    async def fake_start_conversation_run(**kwargs: Any) -> None:
        started.update(kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fake_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": 1,
            "method": "run.start",
            "params": {"input": {"messages": [{"role": "user", "content": "hi"}]}},
        },
    )

    assert response.status_code == 200
    assert response.headers["x-stream-protocol"] == "langgraph_v3"
    payload = response.json()
    assert payload["type"] == "success"
    assert payload["id"] == 1
    assert payload["result"]["multitask_strategy"] == "reject"
    assert payload["result"]["thread_id"] == str(conversation.id)
    assert uuid.UUID(payload["result"]["run_id"])
    assert response.headers["x-run-id"] == payload["result"]["run_id"]
    assert started["run_id"] == uuid.UUID(payload["result"]["run_id"])
    assert started["conversation_id"] == conversation.id
    assert started["input_payload"] == {"messages": [{"role": "user", "content": "hi"}]}
    assert started["executor_fn"].__name__ == "execute_agent_stream_langgraph"


@pytest.mark.asyncio
async def test_run_start_command_rejects_active_run(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_conversation(db)
    db.add(
        ConversationRun(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="running",
            is_active=True,
        )
    )
    await db.commit()

    async def fail_start_conversation_run(**_kwargs: Any) -> None:
        raise AssertionError("active run rejection must happen before worker start")

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fail_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": 2,
            "method": "run.start",
            "params": {"input": {"messages": [{"role": "user", "content": "hi"}]}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "error",
        "id": 2,
        "error": {
            "code": "MULTITASK_REJECTED",
            "message": "Conversation already has an active run",
        },
    }


@pytest.mark.asyncio
async def test_command_rejects_unknown_methods_with_agent_protocol_error(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={"id": "cmd-2", "method": "run.teleport", "params": {}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "error",
        "id": "cmd-2",
        "error": {
            "code": "UNSUPPORTED_COMMAND",
            "message": "Unsupported command method: run.teleport",
        },
    }


@pytest.mark.asyncio
async def test_thread_state_and_history_use_sdk_compatible_shapes(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_conversation(db)
    state_url = f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
    fake_graph = _FakeStateGraph()

    async def fake_resolve_agent_context(
        _db: AsyncSession,
        conversation_id: uuid.UUID,
        _user: Any,
        *,
        checkpoint_id: str | None = None,
    ) -> AgentConfig:
        assert checkpoint_id is None
        return AgentConfig(
            provider="openai",
            model_name="gpt-4o",
            api_key=None,
            base_url=None,
            system_prompt="You are helpful.",
            tools_config=[],
            thread_id=str(conversation_id),
            agent_id=str(conversation.agent_id),
            user_id=str(TEST_USER_ID),
        )

    async def fake_prepare_agent(
        cfg: AgentConfig,
        *,
        messages_history: list[dict[str, str]],
        is_trigger_mode: bool = False,
    ) -> tuple[_FakeStateGraph, list[Any], dict[str, Any]]:
        assert cfg.thread_id == str(conversation.id)
        assert messages_history == []
        assert is_trigger_mode is False
        return fake_graph, [], {"configurable": {"thread_id": cfg.thread_id}}

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state.resolve_agent_context",
        fake_resolve_agent_context,
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state._prepare_agent",
        fake_prepare_agent,
    )

    state_response = await client.get(state_url)
    update_response = await client.post(
        state_url,
        json={
            "values": {"todos": [{"id": "t1", "content": "plan"}]},
            "task_id": "task-state-1",
        },
    )
    history_response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/history",
        json={"limit": 1},
    )

    assert state_response.status_code == 200
    state = state_response.json()
    assert state["values"] == {"messages": []}
    assert state["next"] == []
    assert state["tasks"] == []
    assert state["metadata"]["conversation_id"] == str(conversation.id)

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["values"]["messages"] == []
    assert updated["values"]["todos"] == [{"id": "t1", "content": "plan"}]
    assert updated["checkpoint"]["checkpoint_id"] == "ck-updated"
    assert fake_graph.updates == [
        (
            {"configurable": {"thread_id": str(conversation.id)}},
            {"todos": [{"id": "t1", "content": "plan"}]},
            "__start__",
            "task-state-1",
        )
    ]

    assert history_response.status_code == 200
    assert history_response.json() == []


@pytest.mark.asyncio
async def test_thread_state_reads_checkpointer_messages(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    conversation = await _seed_conversation(db)
    messages = [
        HumanMessage(content="Hello", id=str(uuid.uuid4())),
        AIMessage(content="Hi", id=str(uuid.uuid4())),
    ]

    async def alist(_config: Any) -> Any:
        yield type(
            "CheckpointTuple",
            (),
            {
                "config": {"configurable": {"checkpoint_id": "ck1"}},
                "parent_config": None,
                "checkpoint": {"channel_values": {"messages": messages}},
            },
        )()

    with (
        patch("app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer") as get_cp,
        patch("app.routers.conversation_agent_protocol_state.get_checkpointer") as get_history_cp,
    ):
        get_cp.return_value.alist = alist
        get_history_cp.return_value.alist = alist
        state_response = await client.get(
            f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
        )
        history_response = await client.post(
            f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/history",
            json={"limit": 1},
        )

    assert state_response.status_code == 200
    values = state_response.json()["values"]
    assert values["messages"][0]["type"] == "human"
    assert values["messages"][0]["content"] == "Hello"
    assert values["messages"][1]["type"] == "ai"
    assert values["messages"][1]["content"] == "Hi"

    assert history_response.status_code == 200
    assert history_response.json()[0]["values"] == values


@pytest.mark.asyncio
async def test_thread_state_falls_back_to_legacy_trace_when_checkpointer_is_empty(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex
    await trace_storage.append_events(
        db,
        conversation_id=conversation.id,
        assistant_msg_id=run_id,
        events_chunk=[
            {
                "id": f"{run_id}-1",
                "event": event_names.MESSAGE_START,
                "data": {
                    "id": run_id,
                    "role": "assistant",
                    "input": {
                        "messages": [
                            {"role": "user", "content": "legacy hello", "id": "legacy-user-1"}
                        ]
                    },
                },
            },
            {
                "id": f"{run_id}-2",
                "event": event_names.CONTENT_DELTA,
                "data": {"delta": "legacy hi"},
            },
            {
                "id": f"{run_id}-3",
                "event": event_names.MESSAGE_END,
                "data": {
                    "content": "legacy hi",
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    "status": "completed",
                },
            },
        ],
        status="completed",
    )
    await db.commit()

    with patch(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        side_effect=RuntimeError("checkpointer unavailable"),
    ):
        response = await client.get(
            f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state"
        )

    assert response.status_code == 200
    messages = response.json()["values"]["messages"]
    assert messages[0]["type"] == "human"
    assert messages[0]["content"] == "legacy hello"
    assert messages[1]["type"] == "ai"
    assert messages[1]["content"] == "legacy hi"
    assert messages[1]["usage_metadata"]["input_tokens"] == 3


@pytest.mark.asyncio
async def test_protocol_stream_replays_stored_canonical_events_with_filters(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=run_id,
            events=[
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=1,
                    method="messages",
                    namespace=["agent"],
                    data={"chunk": "hello"},
                    event_id="upstream-1",
                ),
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=2,
                    method="tools",
                    namespace=["agent"],
                    data={"event": "tool-started"},
                    event_id="upstream-2",
                ),
            ],
            last_event_id="upstream-2",
            status="completed",
        )
    )
    await db.commit()

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/stream/events",
        json={"channels": ["messages"], "namespaces": [["agent"]], "depth": 0},
    )

    assert response.status_code == 200
    assert response.headers["x-stream-protocol"] == "langgraph_v3"
    assert response.headers["x-resume-mode"] == "replay"
    assert "event: message" in response.text
    assert '"method":"messages"' in response.text
    assert "hello" in response.text
    assert "tool-started" not in response.text


@pytest.mark.asyncio
async def test_protocol_stream_attaches_live_broker_with_filters(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
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
    broker = broker_registry.get_or_create(str(run_id), conversation_id=str(conversation.id))
    broker.publish_nowait(
        {
            "id": "upstream-live-1",
            "event": "message",
            "data": {
                "type": "event",
                "method": "messages",
                "params": {"namespace": [], "data": {"chunk": "hello"}},
                "seq": 1,
                "event_id": "upstream-live-1",
            },
        }
    )
    broker.publish_nowait(
        {
            "id": "upstream-live-2",
            "event": "message",
            "data": {
                "type": "event",
                "method": "tools",
                "params": {"namespace": [], "data": {"event": "tool-started"}},
                "seq": 2,
                "event_id": "upstream-live-2",
            },
        }
    )

    async def close_broker() -> None:
        await asyncio.sleep(0.01)
        broker.close()

    closer = asyncio.create_task(close_broker())
    try:
        async with client.stream(
            "POST",
            f"/api/conversations/{conversation.id}/langgraph/threads/"
            f"{conversation.id}/stream/events",
            json={"channels": ["messages"]},
        ) as response:
            assert response.status_code == 200
            assert response.headers["x-stream-protocol"] == "langgraph_v3"
            assert response.headers["x-resume-mode"] == "live"
            assert response.headers["x-run-id"] == str(run_id)
            body = (await response.aread()).decode()
    finally:
        await closer

    assert "event: message" in body
    assert '"method":"messages"' in body
    assert "hello" in body
    assert "tool-started" not in body


def test_protocol_stream_projects_live_legacy_terminal_event() -> None:
    run_id = uuid.uuid4().hex
    thread_id = str(uuid.uuid4())
    event: BrokeredEvent = {
        "id": f"{run_id}-canceled",
        "event": event_names.MESSAGE_END,
        "data": {"usage": {}, "content": "", "status": "canceled"},
    }

    events = protocol_events_from_broker(
        event,
        run_id=run_id,
        thread_id=thread_id,
    )

    assert len(events) == 1
    assert events[0]["method"] == "lifecycle"
    assert events[0]["data"]["status"] == "canceled"
    assert events[0]["thread_id"] == thread_id


def _protocol_broker_event(
    *,
    run_id: str,
    method: str,
    seq: int,
    data: dict[str, Any],
) -> BrokeredEvent:
    event_id = f"{run_id}-{seq}"
    return {
        "id": event_id,
        "event": "message",
        "data": {
            "type": "event",
            "method": method,
            "params": {"namespace": [], "data": data},
            "seq": seq,
            "event_id": event_id,
        },
    }


@pytest.mark.asyncio
async def test_protocol_lifecycle_stream_survives_broker_rotation(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import conversation_agent_protocol_thread_stream as thread_stream

    conversation = await _seed_conversation(db)
    first_run_id = uuid.uuid4().hex
    second_run_id = uuid.uuid4().hex
    first_broker = broker_registry.get_or_create(
        first_run_id,
        conversation_id=str(conversation.id),
    )
    first_broker.publish_nowait(
        _protocol_broker_event(
            run_id=first_run_id,
            method="lifecycle",
            seq=1,
            data={"event": "interrupted", "marker": "first-run"},
        )
    )

    async def is_disconnected() -> bool:
        return False

    async def no_replay_events(_conversation_id: uuid.UUID) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(thread_stream, "_load_replay_events", no_replay_events)

    stream = thread_stream.protocol_thread_stream_generator(
        conversation_id=conversation.id,
        thread_id=str(conversation.id),
        params={"channels": ["lifecycle", "messages"]},
        after_id=None,
        is_disconnected=is_disconnected,
    )
    try:
        assert "first-run" in await asyncio.wait_for(anext(stream), timeout=1.0)
        first_broker.close()

        second_broker = broker_registry.get_or_create(
            second_run_id,
            conversation_id=str(conversation.id),
        )
        second_broker.publish_nowait(
            _protocol_broker_event(
                run_id=second_run_id,
                method="lifecycle",
                seq=1,
                data={"event": "completed", "marker": "second-run"},
            )
        )

        assert "second-run" in await asyncio.wait_for(anext(stream), timeout=1.0)
        second_broker.close()
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_protocol_lifecycle_stream_projects_live_events_after_numeric_since(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import conversation_agent_protocol_thread_stream as thread_stream

    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex

    async def is_disconnected() -> bool:
        return False

    async def no_replay_events(_conversation_id: uuid.UUID) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(thread_stream, "_load_replay_events", no_replay_events)
    broker = broker_registry.get_or_create(run_id, conversation_id=str(conversation.id))
    broker.publish_nowait(
        _protocol_broker_event(
            run_id=run_id,
            method="lifecycle",
            seq=1,
            data={"event": "completed", "marker": "live-after-since"},
        )
    )

    stream = thread_stream.protocol_thread_stream_generator(
        conversation_id=conversation.id,
        thread_id=str(conversation.id),
        params={"channels": ["lifecycle"], "since": 5},
        after_id=None,
        is_disconnected=is_disconnected,
    )
    try:
        chunk = await asyncio.wait_for(anext(stream), timeout=1.0)
        assert "live-after-since" in chunk
        assert '"seq":6' in chunk
    finally:
        broker.close()
        await stream.aclose()


@pytest.mark.asyncio
async def test_protocol_lifecycle_stream_uses_broker_cursor_for_live_resume(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import conversation_agent_protocol_thread_stream as thread_stream

    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex

    async def is_disconnected() -> bool:
        return False

    async def no_replay_events(_conversation_id: uuid.UUID) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(thread_stream, "_load_replay_events", no_replay_events)
    broker = broker_registry.get_or_create(run_id, conversation_id=str(conversation.id))
    broker.publish_nowait(
        {
            "id": "broker-live-1",
            "event": "message",
            "data": {
                "type": "event",
                "method": "lifecycle",
                "params": {"namespace": [], "data": {"event": "running"}},
                "seq": 1,
                "event_id": "protocol-wire-1",
            },
        }
    )

    stream = thread_stream.protocol_thread_stream_generator(
        conversation_id=conversation.id,
        thread_id=str(conversation.id),
        params={"channels": ["lifecycle"]},
        after_id=None,
        is_disconnected=is_disconnected,
    )
    try:
        chunk = await asyncio.wait_for(anext(stream), timeout=1.0)
        assert chunk.startswith("id: broker-live-1\n")
        assert '"event_id":"protocol-wire-1"' in chunk
    finally:
        broker.close()
        await stream.aclose()


@pytest.mark.asyncio
async def test_protocol_lifecycle_stream_throttles_idle_replay_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import conversation_agent_protocol_thread_stream as thread_stream

    load_count = 0

    async def fake_load_replay_events(_conversation_id: uuid.UUID) -> list[dict[str, Any]]:
        nonlocal load_count
        load_count += 1
        return []

    async def is_disconnected() -> bool:
        return False

    monkeypatch.setattr(thread_stream, "_load_replay_events", fake_load_replay_events)

    stream = thread_stream.protocol_thread_stream_generator(
        conversation_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        params={"channels": ["lifecycle"]},
        after_id=None,
        is_disconnected=is_disconnected,
    )
    task = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(0.16)
        assert load_count == 1
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await stream.aclose()


@pytest.mark.asyncio
async def test_protocol_lifecycle_stream_backs_off_idle_replay_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import conversation_agent_protocol_thread_stream as thread_stream

    load_count = 0

    async def fake_load_replay_events(_conversation_id: uuid.UUID) -> list[dict[str, Any]]:
        nonlocal load_count
        load_count += 1
        return []

    async def is_disconnected() -> bool:
        return False

    monkeypatch.setattr(thread_stream, "_THREAD_STREAM_REPLAY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(thread_stream, "_THREAD_STREAM_POLL_SECONDS", 0.001)
    monkeypatch.setattr(thread_stream, "_load_replay_events", fake_load_replay_events)

    stream = thread_stream.protocol_thread_stream_generator(
        conversation_id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        params={"channels": ["lifecycle"]},
        after_id=None,
        is_disconnected=is_disconnected,
    )
    task = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(0.09)
        assert load_count <= 4
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await stream.aclose()


@pytest.mark.asyncio
async def test_protocol_stream_replays_when_active_run_finishes_before_broker_attach(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4()
    run = ConversationRun(
        id=run_id,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="resume",
        status="running",
        is_active=True,
    )
    db.add(run)
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run_id),
            events=[
                stored_protocol_event(
                    run_id=str(run_id),
                    thread_id=str(conversation.id),
                    seq=1,
                    method="messages",
                    namespace=[],
                    data={"chunk": "done"},
                    event_id="resume-done-1",
                )
            ],
            last_event_id="resume-done-1",
            status="completed",
        )
    )
    await db.commit()

    async def finish_run() -> None:
        await asyncio.sleep(0.05)
        run.status = "completed"
        run.is_active = False
        await db.commit()

    finisher = asyncio.create_task(finish_run())
    try:
        response = await client.post(
            f"/api/conversations/{conversation.id}/langgraph/threads/"
            f"{conversation.id}/stream/events",
            json={"channels": ["messages"]},
        )
    finally:
        await finisher

    assert response.status_code == 200
    assert response.headers["x-stream-protocol"] == "langgraph_v3"
    assert response.headers["x-resume-mode"] == "replay"
    assert "resume-done-1" in response.text
    assert "done" in response.text


@pytest.mark.asyncio
async def test_protocol_stream_filters_custom_named_channels(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db)
    run_id = uuid.uuid4().hex
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=run_id,
            events=[
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=1,
                    method="custom",
                    data={"name": "artifact", "payload": {"path": "report.md"}},
                    event_id="custom-1",
                ),
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=str(conversation.id),
                    seq=2,
                    method="custom",
                    data={"name": "memory", "payload": {"key": "profile"}},
                    event_id="custom-2",
                ),
            ],
            last_event_id="custom-2",
            status="completed",
        )
    )
    await db.commit()

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/stream/events",
        json={"channels": ["custom:artifact"]},
    )

    assert response.status_code == 200
    assert "report.md" in response.text
    assert "profile" not in response.text
