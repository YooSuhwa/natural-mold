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
async def test_run_start_command_links_attachments_and_forwards_ids_for_backfill(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v3 protocol run.start path (the real chat send) links attachments to
    the conversation, strips them from the LangGraph input, AND forwards the
    attachment ids to the worker so finalize can backfill ``message_id`` (M1).
    Regression guard: the chat UI sends via this agent-protocol path — NOT the
    REST /messages handlers — so the ids must be threaded here too."""

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
    # Attachments are stripped from the LangGraph input (not model-visible here)...
    assert started["input_payload"] == {
        "messages": [{"role": "user", "content": "please review"}],
    }
    # ...but forwarded as ids so the finalize backfill can stamp message_id.
    assert started["attachment_ids"] == [attachment.id]
    assert started["moldy_source"] == "chat"
    assert uuid.UUID(response.json()["result"]["run_id"]) == started["run_id"]
