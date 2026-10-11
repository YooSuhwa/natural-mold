"""Independently check the persisted version parent before resource hydration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update
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


async def test_resource_hydration_rechecks_cached_version_parent(
    db: AsyncSession,
    private_artifact: ConversationArtifact,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    tmp_path: Path,
) -> None:
    own = private_scopes.first
    foreign = private_scopes.second
    agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert agent is not None
    other_agent = await agent_service.create_agent(
        db, AgentCreate(name="B fixture", system_prompt="Fixture", model_id=agent.model_id), foreign
    )
    other_conversation = await create_conversation(db, other_agent.id, context=foreign)
    other_artifact = ConversationArtifact(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        agent_id=other_agent.id,
        conversation_id=other_conversation.id,
        assistant_msg_id="foreign-turn",
        logical_path="foreign.txt",
        display_name="foreign.txt",
        mime_type="text/plain",
        size_bytes=7,
        sha256="b" * 64,
    )
    db.add(other_artifact)
    file_path = tmp_path / "bounded.txt"
    file_path.write_text("B06_DUMMY_REPARENTED_VERSION", encoding="utf-8")
    version = ArtifactVersion(
        artifact_id=private_artifact.id,
        version_number=1,
        object_key=file_path.name,
        original_filename=file_path.name,
        size_bytes=file_path.stat().st_size,
        sha256="a" * 64,
    )
    db.add(version)
    await db.flush()
    private_artifact.current_version_id = version.id
    await db.commit()
    resolver = ChatResourceContextResolver(
        db,
        ResourceContextScope(own, agent.id, collector_case.conversation.id),
        ResourceContextDependencies(LocalArtifactStorageBackend(tmp_path)),
    )
    request = ChatResourceContextRequest(
        resources=[ChatResourceReference(kind="artifact", id=private_artifact.id)]
    )
    control = await resolver.resolve(request)
    assert control.resources[0].text == "B06_DUMMY_REPARENTED_VERSION"
    await db.execute(
        update(ArtifactVersion)
        .where(ArtifactVersion.id == version.id)
        .values(artifact_id=other_artifact.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    persisted_parent = await db.scalar(
        select(ArtifactVersion.artifact_id).where(ArtifactVersion.id == version.id)
    )
    assert persisted_parent == other_artifact.id
    assert version.artifact_id == private_artifact.id
    with pytest.raises(ResourceContextNotFoundError):
        await resolver.resolve(request)
