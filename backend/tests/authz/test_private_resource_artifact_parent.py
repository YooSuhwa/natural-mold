"""Bounded production resolver witness for a malformed persisted artifact parent."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.schemas.agent import AgentCreate
from app.schemas.chat_resource_context import ChatResourceContextRequest, ChatResourceReference
from app.services import agent_service
from app.services.artifact_storage import LocalArtifactStorageBackend
from app.services.chat.conversations import create_conversation
from app.services.chat_resource_context import (
    ChatResourceContextResolver,
    ResourceContextDependencies,
)
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from app.services.chat_resource_context_sources import ResourceContextScope

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_artifact_scope"]


async def test_resource_artifact_denies_foreign_parent_before_file_read(
    db: AsyncSession,
    private_artifact: ConversationArtifact,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    tmp_path: Path,
) -> None:
    # Given real roots in both companies, with an owned A artifact.
    own_agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert own_agent is not None
    foreign_agent = await agent_service.create_agent(
        db,
        AgentCreate(name="Foreign fixture", system_prompt="Fixture", model_id=own_agent.model_id),
        private_scopes.second,
    )
    foreign_conversation = await create_conversation(
        db, foreign_agent.id, context=private_scopes.second
    )
    stored = tmp_path / "bounded.txt"
    stored.write_text("B06_DUMMY_PARENT_B", encoding="utf-8")
    version = ArtifactVersion(
        artifact_id=private_artifact.id,
        version_number=1,
        object_key=stored.name,
        original_filename=stored.name,
        size_bytes=stored.stat().st_size,
        sha256="a" * 64,
    )
    db.add(version)
    await db.flush()
    private_artifact.current_version_id = version.id
    await db.commit()
    resolver = ChatResourceContextResolver(
        db,
        ResourceContextScope(private_scopes.first, own_agent.id, collector_case.conversation.id),
        ResourceContextDependencies(LocalArtifactStorageBackend(tmp_path)),
    )
    request = ChatResourceContextRequest(
        resources=[ChatResourceReference(kind="artifact", id=private_artifact.id)]
    )
    control = await resolver.resolve(request)
    assert control.resources[0].text == "B06_DUMMY_PARENT_B"
    # When persisted artifact A points to exact B Conversation/Agent roots.
    private_artifact.conversation_id = foreign_conversation.id
    private_artifact.agent_id = foreign_agent.id
    await db.commit()
    # Then source hydration must deny before reading the stored object.
    with pytest.raises(ResourceContextNotFoundError):
        await resolver.resolve(request)
