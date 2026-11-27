"""An independent completion stays readable only while its original job and parent are visible."""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.exceptions import AppError
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.notification import Notification
from app.services import chat_notification_access as access
from app.services import chat_notification_outbox as delivery
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.authz.test_independent_job_authority import delegation as delegation

pytestmark = pytest.mark.asyncio

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


@pytest.mark.parametrize(
    "invalidation", ["parent_before_delivery", "parent_after_delivery", "wrong_run"]
)
async def test_independent_result_requires_its_parent_and_completed_resume_chain(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    delegation: Delegation,
    invalidation: str,
) -> None:
    context, _, run, job = delegation
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "storage"))
    store = LocalArtifactStorageBackend()
    run.status, run.is_active = "completed", False
    if invalidation == "wrong_run":
        # A later message from the same actor in the same conversation has every
        # scope ID in common, but isn't a continuation of the independent job.
        run = ConversationRun(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            conversation_id=job.child_conversation_id,
            agent_id=job.child_agent_id,
            source="chat",
            status="completed",
            is_active=False,
        )
        db.add(run)
    await db.flush()
    doc = tmp_path / "report.txt"
    content = b"immutable result version"
    doc.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    artifact = ConversationArtifact(
        conversation_id=job.child_conversation_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=job.child_agent_id,
        assistant_msg_id=str(run.id),
        logical_path="report.txt",
        display_name=doc.name,
        mime_type="text/plain",
        artifact_kind="document",
        size_bytes=len(content),
        sha256=digest,
    )
    db.add(artifact)
    await db.flush()
    stored = await store.put_file(
        conversation_id=job.child_conversation_id,
        artifact_id=artifact.id,
        version_number=1,
        display_name=doc.name,
        source_path=doc,
        tenant_id=context.tenant_id,
    )
    version = ArtifactVersion(
        artifact_id=artifact.id,
        version_number=1,
        object_key=stored.object_key,
        original_filename=doc.name,
        size_bytes=len(content),
        sha256=digest,
    )
    db.add(version)
    await db.flush()
    artifact.current_version_id = version.id
    await delivery.enqueue_independent_completion(
        db,
        job_id=job.id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        user_id=context.user_id,
        conversation_id=job.child_conversation_id,
        run_id=run.id,
        artifact_id=artifact.id,
        version_id=version.id,
    )
    await db.commit()

    if invalidation == "parent_before_delivery":
        await db.execute(delete(Conversation).where(Conversation.id == job.parent_conversation_id))
        await db.commit()
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=context.tenant_id) == 1
    await db.commit()
    receipt = await db.scalar(select(ChatNotificationOutbox))
    assert receipt is not None
    if invalidation != "parent_after_delivery":
        assert receipt.status == "suppressed" and receipt.notification_id is None
        assert await db.scalar(select(Notification.id)) is None
        return

    assert receipt.status == "delivered" and receipt.notification_id is not None
    stored_bytes, name = await access.get_chat_notification_result(
        db, context, receipt.notification_id
    )
    assert name == doc.name and stored_bytes == content
    await db.execute(delete(Conversation).where(Conversation.id == job.parent_conversation_id))
    await db.commit()
    assert not await access.accessible_work_targets(db, context, [receipt.id])
    for read in (access.open_chat_notification, access.get_chat_notification_result):
        with pytest.raises(AppError) as refused:
            await read(db, context, receipt.notification_id)
        assert refused.value.status == 404
