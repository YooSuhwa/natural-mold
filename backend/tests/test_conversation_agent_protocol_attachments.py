from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID


async def _seed_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="attachments@test.dev", name="Attachment User")
        db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="Attachment Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    conversation = Conversation(agent_id=agent.id, title="Attachment Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


async def _seed_attachment(db: AsyncSession) -> MessageAttachment:
    attachment = MessageAttachment(
        user_id=TEST_USER_ID,
        filename="plan.pdf",
        mime_type="application/pdf",
        size_bytes=1234,
        storage_path="uploads/plan.pdf",
        url="/api/uploads/plan.pdf",
    )
    db.add(attachment)
    await db.commit()
    return attachment


@pytest.mark.asyncio
async def test_run_start_command_links_attachments_without_forwarding_them_to_langgraph(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await _seed_conversation(db)
    attachment = await _seed_attachment(db)
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
            "id": "attach-1",
            "method": "run.start",
            "params": {
                "input": {
                    "messages": [{"role": "user", "content": "please review"}],
                    "attachments": [{"id": str(attachment.id)}],
                }
            },
        },
    )

    await db.refresh(attachment)

    assert response.status_code == 200
    assert attachment.conversation_id == conversation.id
    assert started["input_payload"] == {
        "messages": [{"role": "user", "content": "please review"}],
    }
    assert started["moldy_source"] == "chat"
    assert uuid.UUID(response.json()["result"]["run_id"]) == started["run_id"]
