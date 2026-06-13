from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.services.thread_branch_service import _CheckpointSlim
from tests.conftest import TEST_USER_ID


class _FakeCheckpointer:
    def __init__(self, checkpoints: list[_CheckpointSlim]) -> None:
        self._checkpoints = checkpoints

    async def alist(self, _config: Any) -> AsyncIterator[Any]:
        for checkpoint in self._checkpoints:
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
                    "checkpoint": {"channel_values": {"messages": checkpoint.messages}},
                },
            )()


async def _seed_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="sdk-history@test.dev", name="SDK History")
        db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="SDK History Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    conversation = Conversation(agent_id=agent.id, title="SDK History Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


@pytest.mark.asyncio
async def test_sdk_thread_history_path_returns_checkpoint_snapshots(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_conversation(db)
    parent = _CheckpointSlim(
        checkpoint_id="sdk-ck-user",
        parent_checkpoint_id=None,
        messages=[HumanMessage(id="sdk-user-1", content="hello")],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="sdk-ck-assistant",
        parent_checkpoint_id="sdk-ck-user",
        messages=[
            HumanMessage(id="sdk-user-1", content="hello"),
            AIMessage(id="sdk-assistant-1", content="hi"),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_runtime.get_checkpointer",
        lambda: _FakeCheckpointer([leaf, parent]),
    )

    response = await client.post(
        f"/threads/{conversation.id}/history",
        json={"limit": 2, "metadata": {"source": "sdk-test"}, "checkpoint": None},
    )

    assert response.status_code == 200
    history = response.json()
    assert [state["checkpoint"]["checkpoint_id"] for state in history] == [
        "sdk-ck-assistant",
        "sdk-ck-user",
    ]
    assert [len(state["values"]["messages"]) for state in history] == [2, 1]


@pytest.mark.asyncio
async def test_sdk_thread_history_accepts_configurable_before_cursor(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_conversation(db)
    parent = _CheckpointSlim(
        checkpoint_id="sdk-before-user",
        parent_checkpoint_id=None,
        messages=[HumanMessage(id="sdk-before-user-1", content="hello")],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="sdk-before-assistant",
        parent_checkpoint_id="sdk-before-user",
        messages=[
            HumanMessage(id="sdk-before-user-1", content="hello"),
            AIMessage(id="sdk-before-assistant-1", content="hi"),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_runtime.get_checkpointer",
        lambda: _FakeCheckpointer([leaf, parent]),
    )

    response = await client.post(
        f"/threads/{conversation.id}/history",
        json={"limit": 2, "before": {"configurable": {"checkpoint_id": "sdk-before-assistant"}}},
    )

    assert response.status_code == 200
    history = response.json()
    assert [state["checkpoint"]["checkpoint_id"] for state in history] == ["sdk-before-user"]
