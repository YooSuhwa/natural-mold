from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.user import User
from app.services.artifact_storage import LocalArtifactStorageBackend
from app.services.chat_resource_context import (
    ChatResourceContextResolver,
    ResourceContextDependencies,
    ResourceContextScope,
)
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    ensure_unit_scope,
    unit_context,
)
from tests.conftest import TEST_USER_ID


@dataclass(frozen=True, slots=True)
class SeededResourceContext:
    user: User
    agent: Agent
    conversation: Conversation


async def seed_resource_context(db: AsyncSession) -> SeededResourceContext:
    user = User(id=TEST_USER_ID, email="owner@test.com", name="Owner")
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add_all([user, model])
    await db.flush()
    await ensure_unit_scope(db, CurrentUser(id=user.id, email=user.email, name=user.name))
    agent = Agent(
        user_id=user.id,
        name="Agent",
        system_prompt="system",
        model_id=model.id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(
        agent_id=agent.id,
        title="Current",
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=agent.user_id,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.flush()
    return SeededResourceContext(user=user, agent=agent, conversation=conversation)


def resource_context_resolver(
    db: AsyncSession,
    seeded: SeededResourceContext,
    storage_root: Path,
) -> ChatResourceContextResolver:
    return ChatResourceContextResolver(
        db,
        ResourceContextScope(
            context=unit_context(seeded.user.id),
            agent_id=seeded.agent.id,
            conversation_id=seeded.conversation.id,
        ),
        ResourceContextDependencies(
            artifact_storage=LocalArtifactStorageBackend(storage_root),
        ),
    )


async def seed_context_attachment(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    storage_root: Path,
) -> MessageAttachment:
    stored = storage_root / "context.txt"
    stored.write_text("enqueue snapshot", encoding="utf-8")
    attachment = MessageAttachment(
        user_id=TEST_USER_ID,
        conversation_id=conversation_id,
        message_id=str(uuid.uuid4()),
        filename="context.txt",
        mime_type="text/plain",
        size_bytes=stored.stat().st_size,
        storage_path=str(stored),
        url="/api/uploads/context",
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
    )
    db.add(attachment)
    await db.flush()
    return attachment


def resource_context_run_start_body(
    attachment_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    return {
        "id": request_id,
        "method": "run.start",
        "params": {
            "client_request_id": request_id,
            "multitask_strategy": "enqueue",
            "input": {
                "messages": [{"role": "user", "content": "use context"}],
                "resource_context": [
                    {"kind": "file", "id": str(attachment_id), "label": "shown label"}
                ],
            },
        },
    }
