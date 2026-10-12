from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.services.artifacts.summary import _summaries_from_artifacts
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession


@pytest.mark.asyncio
async def test_retained_artifact_summary_uses_conversation_agent_name_when_agent_is_null() -> None:
    # Given: retained artifact history whose Agent FK was detached by purge.
    conversation_id = uuid4()
    artifact_id = uuid4()
    version_id = uuid4()
    async with TestSession() as db:
        conversation = Conversation(
            id=conversation_id,
            agent_id=None,
            user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            agent_name_snapshot="Retained Agent",
            agent_lifecycle_state="purged",
            agent_purged_at=datetime.now(UTC),
            last_activity_at=datetime.now(UTC),
        )
        artifact = ConversationArtifact(
            id=artifact_id,
            user_id=TEST_USER_ID,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            agent_id=None,
            conversation_id=conversation_id,
            assistant_msg_id="retained-run",
            logical_path="reports/retained.txt",
            display_name="retained.txt",
            mime_type="text/plain",
            artifact_kind="document",
            size_bytes=8,
            sha256="a" * 64,
            current_version_id=version_id,
            status="ready",
        )
        db.add_all([conversation, artifact])
        await db.flush()
        db.add(
            ArtifactVersion(
                id=version_id,
                artifact_id=artifact_id,
                version_number=1,
                storage_provider="local",
                object_key="retained.txt",
                original_filename="retained.txt",
                size_bytes=8,
                sha256="a" * 64,
            )
        )
        await db.commit()

        # When: the retained artifact is projected through the normal summary path.
        summaries = await _summaries_from_artifacts(db, [artifact], include_names=True)

    # Then: the nullable identity and immutable Conversation name both survive.
    assert [(item.agent_id, item.agent_name) for item in summaries] == [(None, "Retained Agent")]
