"""Artifact library and runtime persistence have an explicit organization boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.services.artifacts.errors import ArtifactNotFoundError
from app.services.artifacts.library import (
    _get_owned_artifact,
    _get_owned_artifact_with_version,
    set_artifact_favorite,
)

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


@pytest.fixture
async def private_artifact(db: AsyncSession, collector_case: CollectorCase) -> ConversationArtifact:
    conversation = collector_case.conversation
    assert conversation.agent_id is not None
    artifact = ConversationArtifact(
        user_id=conversation.user_id,
        org_id=conversation.org_id,
        tenant_id=conversation.tenant_id,
        agent_id=conversation.agent_id,
        conversation_id=conversation.id,
        assistant_msg_id="fixture-turn",
        logical_path="fixture.txt",
        display_name="fixture.txt",
        mime_type="text/plain",
        size_bytes=7,
        sha256="a" * 64,
    )
    db.add(artifact)
    await db.commit()
    return artifact


async def test_artifact_lookup_rejects_same_owner_foreign_company(
    db: AsyncSession, private_artifact: ConversationArtifact, private_scopes: PrivateScopes
) -> None:
    assert (
        await _get_owned_artifact(db, context=private_scopes.first, artifact_id=private_artifact.id)
        is private_artifact
    )
    with pytest.raises(ArtifactNotFoundError):
        await _get_owned_artifact(
            db, context=private_scopes.second, artifact_id=private_artifact.id
        )


async def test_artifact_favorite_rejects_same_owner_foreign_company(
    db: AsyncSession, private_artifact: ConversationArtifact, private_scopes: PrivateScopes
) -> None:
    with pytest.raises(ArtifactNotFoundError):
        await set_artifact_favorite(
            db, context=private_scopes.second, artifact_id=private_artifact.id, is_favorite=True
        )
    await db.refresh(private_artifact)
    assert private_artifact.is_favorite is False


async def test_owned_artifact_cannot_read_another_artifacts_version(
    db: AsyncSession, private_artifact: ConversationArtifact, private_scopes: PrivateScopes
) -> None:
    foreign = private_scopes.second
    other = ConversationArtifact(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        agent_id=private_artifact.agent_id,
        conversation_id=private_artifact.conversation_id,
        assistant_msg_id="other-turn",
        logical_path="other.txt",
        display_name="other.txt",
        mime_type="text/plain",
        size_bytes=7,
        sha256="b" * 64,
    )
    db.add(other)
    await db.flush()
    version = ArtifactVersion(
        artifact_id=other.id,
        version_number=1,
        object_key="other/private-content.txt",
        original_filename="other.txt",
        size_bytes=7,
        sha256="b" * 64,
    )
    db.add(version)
    await db.flush()
    private_artifact.current_version_id = version.id
    await db.commit()
    with pytest.raises(ArtifactNotFoundError):
        await _get_owned_artifact_with_version(
            db, context=private_scopes.first, artifact_id=private_artifact.id
        )
