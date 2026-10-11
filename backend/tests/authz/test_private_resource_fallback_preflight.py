"""F1 external witness; run through official wrapper with dedicated PYTHONPATH."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock
from uuid import uuid4

import pytest
from anyio import Path as AsyncPath
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.schemas.chat_resource_context import ChatResourceContextRequest, ChatResourceReference
from app.services import chat_resource_context_sources
from app.services.artifact_storage import LocalArtifactStorageBackend
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


def test_exact_production_provenance() -> None:
    expected = Path(__file__).resolve().parents[2]
    assert Path(chat_resource_context_sources.__file__).resolve().is_relative_to(expected)


@pytest.mark.parametrize("explicit_version", [False, True])
async def test_missing_fallback_version_denial_precedes_pending_sql(
    db: AsyncSession,
    private_artifact: ConversationArtifact,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    tmp_path: Path,
    explicit_version: bool,
) -> None:
    own = private_scopes.first
    agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert agent is not None
    path = tmp_path / "f1-version.txt"
    await AsyncPath(path).write_text("F1_R18_BOUNDED_FALLBACK", encoding="utf-8")
    version = ArtifactVersion(
        artifact_id=private_artifact.id,
        version_number=1,
        object_key=path.name,
        original_filename=path.name,
        size_bytes=path.stat().st_size,
        sha256="a" * 64,
    )
    db.add(version)
    await db.commit()
    assert private_artifact.current_version_id is None
    resolver = ChatResourceContextResolver(
        db,
        ResourceContextScope(own, agent.id, collector_case.conversation.id),
        ResourceContextDependencies(LocalArtifactStorageBackend(tmp_path)),
    )
    normal = ChatResourceContextRequest(
        resources=[ChatResourceReference(kind="artifact", id=private_artifact.id)]
    )
    control = await resolver.resolve(normal)
    assert control.resources[0].text == "F1_R18_BOUNDED_FALLBACK"
    # Valid physical parent exists, but the original artifact no longer has a version.
    other = ConversationArtifact(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        agent_id=agent.id,
        conversation_id=collector_case.conversation.id,
        assistant_msg_id="other-f1-turn",
        logical_path="other-f1.txt",
        display_name="other-f1.txt",
        mime_type="text/plain",
        size_bytes=path.stat().st_size,
        sha256="b" * 64,
    )
    db.add(other)
    await db.flush()
    await db.execute(
        update(ArtifactVersion)
        .where(ArtifactVersion.id == version.id)
        .values(artifact_id=other.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert version.artifact_id == private_artifact.id
    persisted_parent = await db.scalar(
        select(ArtifactVersion.artifact_id).where(ArtifactVersion.id == version.id)
    )
    assert persisted_parent == other.id
    original = agent.name
    agent.name = "F1_R18_PENDING_PRIVATE_SQL"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    request = ChatResourceContextRequest(
        resources=[
            ChatResourceReference(
                kind="artifact",
                id=private_artifact.id,
                version_id=uuid4() if explicit_version else None,
            )
        ]
    )
    try:
        with pytest.raises(ResourceContextNotFoundError):
            await resolver.resolve(request)
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == agent.id))
    pending = agent in db.dirty
    await db.rollback()
    assert flush.call_count == 0 and stored == original and pending
