from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.services.ag_ui_attachment_access import readable_artifact_versions
from tests.authz.test_independent_job_authority import delegation as delegation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.test_private_artifact_scope"]


async def _version(db: AsyncSession, file_id: UUID, number: int) -> UUID:
    row = ArtifactVersion(
        artifact_id=file_id,
        version_number=number,
        object_key="private/storage-key",
        original_filename="private-filename",
        size_bytes=1,
        sha256="a" * 64,
        metadata_json={"image_description": "private caption"},
    )
    db.add(row)
    await db.flush()
    return row.id


async def test_replay_pointers_follow_private_version_and_conversation(
    db: AsyncSession, private_artifact: ConversationArtifact, private_scopes: PrivateScopes
) -> None:
    file = private_artifact
    run = uuid4()
    file.assistant_msg_id = str(run)
    old = await _version(db, file.id, 1)
    latest = await _version(db, file.id, 2)
    await db.commit()

    async def pointers(
        context: OrgContext = private_scopes.first,
        conversation_id: UUID = file.conversation_id,
        run_id: UUID = run,
    ) -> frozenset[tuple[str, str]]:
        return await readable_artifact_versions(
            db, context=context, conversation_id=conversation_id, run_id=run_id
        )

    assert await pointers() == {(str(file.id), str(latest))}
    assert await pointers(private_scopes.second) == set()
    assert await pointers(conversation_id=uuid4()) == set()
    assert await pointers(run_id=uuid4()) == set()
    file.current_version_id = old
    await db.commit()
    assert await pointers() == {(str(file.id), str(old))}
    file.current_version_id = uuid4()
    await db.commit()
    assert await pointers() == set()
    file.current_version_id = latest
    file.status = "deleted"
    await db.commit()
    assert await pointers() == set()


async def test_independent_file_pointer_disappears_when_delegation_is_revoked(
    db: AsyncSession,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
) -> None:
    context, parent, child, job = delegation
    file = ConversationArtifact(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=job.child_agent_id,
        conversation_id=job.child_conversation_id,
        assistant_msg_id=str(child.id),
        logical_path="private/child.txt",
        display_name="private child report",
        mime_type="text/plain",
        size_bytes=1,
        sha256="a" * 64,
    )
    db.add(file)
    await db.flush()
    version = await _version(db, file.id, 1)
    file.current_version_id = version
    await db.commit()

    async def pointers(run_id: UUID) -> frozenset[tuple[str, str]]:
        return await readable_artifact_versions(
            db, context=context, conversation_id=job.child_conversation_id, run_id=run_id
        )

    assert await pointers(child.id) == {(str(file.id), str(version))}
    file.assistant_msg_id = str(parent.id)
    await db.commit()
    assert await pointers(parent.id) == set()
    file.assistant_msg_id = str(child.id)
    await db.execute(
        delete(AgentSubAgentLink).where(
            AgentSubAgentLink.parent_agent_id == job.parent_agent_id,
            AgentSubAgentLink.sub_agent_id == job.child_agent_id,
        )
    )
    await db.commit()
    assert await pointers(child.id) == set()
