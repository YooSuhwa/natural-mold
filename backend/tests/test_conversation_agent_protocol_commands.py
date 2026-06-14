from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID


async def _seed_protocol_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="protocol@test.dev", name="Protocol User")
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


@pytest.mark.asyncio
async def test_input_respond_command_starts_langgraph_resume_run(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent_run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="interrupted",
        is_active=False,
        interrupt_id="intr-1",
    )
    db.add(parent_run)
    await db.commit()
    started = {}

    async def fake_start_conversation_run(**kwargs):
        started.update(kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fake_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "resume-1",
            "method": "input.respond",
            "params": {
                "namespace": [],
                "interrupt_id": "intr-1",
                "response": {"decisions": [{"type": "approve"}]},
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["x-stream-protocol"] == "langgraph_v3"
    payload = response.json()
    assert payload["type"] == "success"
    assert payload["id"] == "resume-1"
    assert payload["result"]["thread_id"] == str(conversation.id)
    assert uuid.UUID(payload["result"]["run_id"])
    assert response.headers["x-run-id"] == payload["result"]["run_id"]
    assert started["run_id"] == uuid.UUID(payload["result"]["run_id"])
    assert started["conversation_id"] == conversation.id
    assert started["input_payload"] == {"decisions": [{"type": "approve"}]}
    assert started["moldy_source"] == "resume"
    assert started["executor_fn"].__name__ == "resume_agent_stream_langgraph"


@pytest.mark.asyncio
async def test_input_respond_command_preserves_batched_interrupt_responses(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent_run_id = uuid.uuid4()
    db.add(
        ConversationRun(
            id=parent_run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="interrupted",
            is_active=False,
            interrupt_id="intr-a",
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
                    seq=1,
                    method="input.requested",
                    namespace=["root"],
                    data={"interrupt_id": "intr-a", "payload": {"question": "A"}},
                ),
                stored_protocol_event(
                    run_id=str(parent_run_id),
                    thread_id=str(conversation.id),
                    seq=2,
                    method="input.requested",
                    namespace=["subgraph:worker"],
                    data={"interrupt_id": "intr-b", "payload": {"question": "B"}},
                ),
            ],
            status="completed",
        )
    )
    await db.commit()
    started = {}

    async def fake_start_conversation_run(**kwargs):
        started.update(kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fake_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "resume-many",
            "method": "input.respond",
            "params": {
                "responses": [
                    {
                        "namespace": ["subgraph:worker"],
                        "interrupt_id": "intr-b",
                        "response": {"decisions": [{"type": "reject", "message": "no"}]},
                    },
                    {
                        "namespace": ["root"],
                        "interrupt_id": "intr-a",
                        "response": {"decisions": [{"type": "approve"}]},
                    },
                ]
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["type"] == "success"
    assert started["input_payload"] == {
        "intr-a": {"decisions": [{"type": "approve"}]},
        "intr-b": {"decisions": [{"type": "reject", "message": "no"}]},
    }


@pytest.mark.asyncio
async def test_input_respond_command_restores_redacted_edit_placeholders(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent_run_id = uuid.uuid4()
    db.add(
        ConversationRun(
            id=parent_run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="interrupted",
            is_active=False,
            interrupt_id="intr-secret",
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
                    seq=1,
                    method="input.requested",
                    namespace=["root"],
                    data={
                        "interrupt_id": "intr-secret",
                        "payload": {
                            "action_requests": [
                                {
                                    "name": "execute_in_skill",
                                    "args": {
                                        "command": "node scripts/create_docx.cjs",
                                        "api_key": "<redacted>",
                                    },
                                }
                            ],
                            "review_configs": [
                                {
                                    "action_name": "execute_in_skill",
                                    "allowed_decisions": ["approve", "edit", "reject"],
                                }
                            ],
                        },
                    },
                )
            ],
            status="completed",
        )
    )
    await db.commit()
    started = {}

    async def fake_raw_pending_actions_by_interrupt(_conversation, _pending_interrupts):
        return {
            "intr-secret": [
                {
                    "name": "execute_in_skill",
                    "args": {
                        "command": "node scripts/create_docx.cjs",
                        "api_key": "raw-secret-value",
                    },
                }
            ]
        }

    async def fake_start_conversation_run(**kwargs):
        started.update(kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_resume_redaction._raw_pending_actions_by_interrupt",
        fake_raw_pending_actions_by_interrupt,
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fake_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "resume-edit-secret",
            "method": "input.respond",
            "params": {
                "namespace": ["root"],
                "interrupt_id": "intr-secret",
                "response": {
                    "decisions": [
                        {
                            "type": "edit",
                            "edited_action": {
                                "name": "execute_in_skill",
                                "args": {
                                    "command": "node scripts/updated_docx.cjs",
                                    "api_key": "<redacted>",
                                },
                            },
                        }
                    ]
                },
            },
        },
    )

    assert response.status_code == 200
    assert started["input_payload"] == {
        "decisions": [
            {
                "type": "edit",
                "edited_action": {
                    "name": "execute_in_skill",
                    "args": {
                        "command": "node scripts/updated_docx.cjs",
                        "api_key": "raw-secret-value",
                    },
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_input_respond_command_rejects_stale_namespace(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent_run_id = uuid.uuid4()
    db.add(
        ConversationRun(
            id=parent_run_id,
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
            assistant_msg_id=str(parent_run_id),
            events=[
                stored_protocol_event(
                    run_id=str(parent_run_id),
                    thread_id=str(conversation.id),
                    seq=1,
                    method="input.requested",
                    namespace=["subgraph:worker"],
                    data={"interrupt_id": "intr-1", "payload": {"question": "approve?"}},
                )
            ],
            status="completed",
        )
    )
    await db.commit()

    async def fail_start_conversation_run(**_kwargs):
        raise AssertionError("stale namespace must reject before worker start")

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fail_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "resume-stale-ns",
            "method": "input.respond",
            "params": {
                "namespace": ["wrong"],
                "interrupt_id": "intr-1",
                "response": {"decisions": [{"type": "approve"}]},
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "error",
        "id": "resume-stale-ns",
        "error": {
            "code": "STALE_INTERRUPT",
            "message": "input.respond namespace does not match the pending interrupt",
        },
    }
    resume_rows = await db.scalars(
        select(ConversationRun).where(
            ConversationRun.conversation_id == conversation.id,
            ConversationRun.source == "resume",
        )
    )
    assert list(resume_rows) == []


@pytest.mark.asyncio
async def test_input_respond_command_rejects_unknown_batched_interrupt(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    parent_run_id = uuid.uuid4()
    db.add(
        ConversationRun(
            id=parent_run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="interrupted",
            is_active=False,
            interrupt_id="intr-a",
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
                    seq=1,
                    method="input.requested",
                    namespace=["root"],
                    data={"interrupt_id": "intr-a", "payload": {"question": "A"}},
                )
            ],
            status="completed",
        )
    )
    await db.commit()

    async def fail_start_conversation_run(**_kwargs):
        raise AssertionError("unknown interrupt must reject before worker start")

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fail_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "resume-unknown",
            "method": "input.respond",
            "params": {
                "responses": [
                    {
                        "namespace": ["root"],
                        "interrupt_id": "intr-a",
                        "response": {"decisions": [{"type": "approve"}]},
                    },
                    {
                        "namespace": ["subgraph:worker"],
                        "interrupt_id": "intr-b",
                        "response": {"decisions": [{"type": "reject"}]},
                    },
                ]
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "error",
        "id": "resume-unknown",
        "error": {
            "code": "STALE_INTERRUPT",
            "message": "input.respond interrupt_id is not pending",
        },
    }
    resume_rows = await db.scalars(
        select(ConversationRun).where(
            ConversationRun.conversation_id == conversation.id,
            ConversationRun.source == "resume",
        )
    )
    assert list(resume_rows) == []


@pytest.mark.asyncio
async def test_run_start_command_rejects_sdk_camel_case_unsupported_multitask_strategy(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)

    async def fail_start_conversation_run(**_kwargs):
        raise AssertionError("unsupported multitaskStrategy must reject before worker start")

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fail_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "run-enqueue",
            "method": "run.start",
            "params": {
                "input": {"messages": [{"role": "user", "content": "hi"}]},
                "multitaskStrategy": "enqueue",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "error",
        "id": "run-enqueue",
        "error": {
            "code": "UNSUPPORTED_MULTITASK_STRATEGY",
            "message": "Unsupported multitask strategy: enqueue",
        },
    }


@pytest.mark.asyncio
async def test_run_start_command_forwards_edit_source_to_worker(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_protocol_conversation(db)
    started = {}

    async def fake_fork_overwrite_input(**kwargs):
        assert kwargs["checkpoint_id"] == "ck-before-user"
        assert [message.content for message in kwargs["append_messages"]] == ["edited prompt"]
        return {"messages": [{"role": "user", "content": "edited prompt"}]}

    async def fake_start_conversation_run(**kwargs):
        started.update(kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_commands._fork_overwrite_input",
        fake_fork_overwrite_input,
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        fake_start_conversation_run,
    )

    response = await client.post(
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands",
        json={
            "id": "run-edit",
            "method": "run.start",
            "params": {
                "checkpoint": {"checkpoint_id": "ck-before-user"},
                "input": {"messages": [{"role": "user", "content": "edited prompt"}]},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["type"] == "success"
    assert started["moldy_source"] == "edit"
    run = await db.get(ConversationRun, uuid.UUID(response.json()["result"]["run_id"]))
    assert run is not None
    assert run.source == "edit"
