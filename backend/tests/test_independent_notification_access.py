"""An independent completion stays readable only while its original job and parent are visible."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.fake import FakeAuthz
from app.authz.org_context import OrgContext
from app.authz.service import AuthzService
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
from tests.authz.d04_support import Unavailable
from tests.authz.test_independent_job_authority import delegation as delegation

pytestmark = pytest.mark.asyncio

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


async def _queue_result(
    db: AsyncSession,
    context: OrgContext,
    job: IndependentChatJob,
    run: ConversationRun,
    tmp_path: Path,
) -> tuple[bytes, str]:
    store = LocalArtifactStorageBackend()
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
    return content, doc.name


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
    content, name = await _queue_result(db, context, job, run, tmp_path)

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
    stored_bytes, stored_name = await access.get_chat_notification_result(
        db, context, receipt.notification_id
    )
    assert stored_name == name and stored_bytes == content
    await db.execute(delete(Conversation).where(Conversation.id == job.parent_conversation_id))
    await db.commit()
    assert not await access.accessible_work_targets(db, context, [receipt.id])
    for read in (access.open_chat_notification, access.get_chat_notification_result):
        with pytest.raises(AppError) as refused:
            await read(db, context, receipt.notification_id)
        assert refused.value.status == 404


async def test_authz_outage_retries_pending_delivery_and_masks_existing_result(
    db: AsyncSession,
    client: AsyncClient,
    delegation: Delegation,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context, _, run, job = delegation
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "storage"))
    run.status, run.is_active = "completed", False
    content, _ = await _queue_result(db, context, job, run, tmp_path)
    receipt = await db.scalar(select(ChatNotificationOutbox))
    assert receipt
    grants = frozenset(
        (f"user:{context.user_id}", "can_run", f"agent:{agent_id}")
        for agent_id in (job.parent_agent_id, job.child_agent_id)
    )
    healthy = AuthzService(FakeAuthz(grants), mode="enforce", enforce_types=frozenset({"agent"}))
    offline = AuthzService(Unavailable(), mode="enforce", enforce_types=frozenset({"agent"}))

    monkeypatch.setattr(runtime, "_current", offline)
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=context.tenant_id) == 1
    await db.commit()
    await db.refresh(receipt)
    assert receipt.status == "pending" and receipt.attempt_count == 1
    assert receipt.notification_id is None and receipt.finished_at is None
    assert await db.scalar(select(Notification.id)) is None
    # Move the stored next attempt into the past instead of sleeping through the retry delay.
    receipt.available_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()

    monkeypatch.setattr(runtime, "_current", healthy)
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=context.tenant_id) == 1
    await db.commit()
    await db.refresh(receipt)
    assert receipt.status == "delivered" and receipt.attempt_count == 2
    assert receipt.notification_id is not None
    notice_id = receipt.notification_id
    assert list(await db.scalars(select(Notification.id))) == [notice_id]
    assert await delivery.drain_chat_notification_outbox(db, tenant_id=context.tenant_id) == 0
    path = f"/api/notifications/{notice_id}"
    assert (await client.get(f"{path}/destination")).status_code == 200
    downloaded = await client.get(f"{path}/result")
    assert downloaded.status_code == 200 and downloaded.content == content

    monkeypatch.setattr(runtime, "_current", offline)
    response = await client.get("/api/notifications")
    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert item["target"]["accessible"] is False and item["target"]["id"] is None
    assert item["target"]["name"] is None and item["link"] is None
    for suffix in ("destination", "result"):
        refused = await client.get(f"{path}/{suffix}")
        assert refused.status_code == 404 and content not in refused.content
    await db.refresh(receipt)
    assert (receipt.status, receipt.attempt_count, receipt.notification_id) == (
        "delivered",
        2,
        notice_id,
    )
    monkeypatch.setattr(runtime, "_current", healthy)
    assert (await client.get(f"{path}/result")).content == content
