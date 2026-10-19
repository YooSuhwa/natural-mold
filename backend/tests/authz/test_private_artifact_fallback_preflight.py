"""F5 cached parent/fallback effect proof with complete valid fixture roots."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock
from uuid import uuid4

import pytest
from anyio import Path as AsyncPath
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.schemas.chat_resource_context import ChatResourceContextRequest, ChatResourceReference
from app.services import chat_resource_context_sources
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
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
SOURCE_PATH = Path(chat_resource_context_sources.__file__).resolve()
SOURCE_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("explicit", [False, True])
async def test_cached_foreign_version_parent_denies_before_sql_effect(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    tmp_path: Path,
    explicit: bool,
) -> None:
    # Given: two complete valid organization graphs, and an owned version hydrated into cache.
    expected = SOURCE_ROOT / "app/services/chat_resource_context_sources.py"
    assert expected == SOURCE_PATH
    own = private_scopes.first
    artifacts: list[ConversationArtifact] = []
    conversations = []
    for context, deployment in ((own, api_case.first), (private_scopes.second, api_case.second)):
        conversation = await create_conversation(db, deployment.agent_id, context=context)
        conversations.append(conversation)
        artifact = ConversationArtifact(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            agent_id=deployment.agent_id,
            conversation_id=conversation.id,
            assistant_msg_id="f5-cached",
            logical_path="cached.txt",
            display_name="cached.txt",
            mime_type="text/plain",
            size_bytes=14,
            sha256="a" * 64,
        )
        db.add(artifact)
        artifacts.append(artifact)
    await db.flush()
    file = tmp_path / "cached.txt"
    await AsyncPath(file).write_text("F5_DUMMY_OWNED", encoding="utf-8")
    version = ArtifactVersion(
        artifact_id=artifacts[0].id,
        version_number=1,
        object_key=file.name,
        original_filename=file.name,
        size_bytes=14,
        sha256="a" * 64,
    )
    db.add(version)
    await db.flush()
    artifacts[0].current_version_id = version.id
    await db.commit()
    resolver = ChatResourceContextResolver(
        db,
        ResourceContextScope(own, api_case.first.agent_id, conversations[0].id),
        ResourceContextDependencies(LocalArtifactStorageBackend(tmp_path)),
    )
    reference = ChatResourceReference(
        kind="artifact", id=artifacts[0].id, version_id=version.id if explicit else None
    )
    request = ChatResourceContextRequest(resources=[reference])
    control = await resolver.resolve(request)
    assert control.resources[0].text == "F5_DUMMY_OWNED"
    await db.execute(
        update(ArtifactVersion)
        .where(ArtifactVersion.id == version.id)
        .values(artifact_id=artifacts[1].id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert version.artifact_id == artifacts[0].id
    persisted_parent = await db.scalar(
        select(ArtifactVersion.artifact_id).where(ArtifactVersion.id == version.id)
    )
    assert persisted_parent == artifacts[1].id
    source_agent = await db.get(Agent, api_case.first.agent_id)
    assert source_agent is not None
    pending = Agent(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        model_id=source_agent.model_id,
        name="F5 pending cached",
        system_prompt="F5_DUMMY",
        runtime_name="f5-cached-" + uuid4().hex,
    )
    db.add(pending)
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    # When: persisted parent differs from cache; actual implicit or explicit version path denies.
    try:
        with pytest.raises(ResourceContextNotFoundError):
            await resolver.resolve(request)
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(
            select(func.count()).select_from(Agent).where(Agent.id == pending.id)
        )
    await db.rollback()
    # Then: the fresh parent denial occurs before pending private content reaches SQL.
    assert (flush.call_count, stored) == (0, 0)
