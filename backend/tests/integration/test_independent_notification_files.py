"""Pinned child results survive file updates and expire on delegation revocation."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.authz.notification_queries import mark_read
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session, tenant_sessions
from app.exceptions import AppError
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.conversation import Conversation
from app.models.conversation_artifact import ConversationArtifact
from app.models.independent_chat_job import IndependentChatJob
from app.models.notification import Notification
from app.services.artifacts.recorder import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.chat_notification_access import (
    get_chat_notification_result,
    open_chat_notification,
)
from app.services.chat_notification_outbox import (
    enqueue_independent_completion,
)
from tests.authz.i06_rls_lab import RlsLab, async_url
from tests.authz.tenant_member_exit_native_fixture import seed_company
from tests.integration.notification_process import kill_after_notice_flush, notification_poller
from tests.integration.test_chat_notification_outbox import (
    Sessions,
)
from tests.integration.test_chat_notification_outbox import (
    completed_job as completed_job,
)
from tests.integration.test_chat_notification_outbox import (
    lab as lab,
)
from tests.integration.test_chat_notification_outbox import (
    sessions as sessions,
)

pytestmark = pytest.mark.integration


async def test_notification_keeps_the_original_file_version_until_delegation_is_revoked(
    sessions: Sessions,
    lab: RlsLab,
    completed_job: tuple[OrgContext, UUID, UUID, UUID],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, room_id, run_id, job_id = completed_job
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "store"))
    output = tmp_path / "outputs"
    output.mkdir()
    report = output / "report.txt"
    private_sessions = tenant_sessions(ctx, maker=sessions)
    async with private_sessions() as reader:
        job = await reader.get(IndependentChatJob, job_id)
        assert job is not None
        parent_id = job.parent_conversation_id
        context = ArtifactRuntimeContext(
            conversation_id=room_id,
            org_context=ctx,
            agent_id=job.child_agent_id,
            assistant_msg_id=str(run_id),
            output_dir=output,
            source_tool_name="write_file",
        )

    recorder = ArtifactDeltaRecorder(session_factory=private_sessions, context=context)
    await recorder.prepare()
    original = b"immutable first result"
    report.write_bytes(original)
    assert await recorder.collect_after_tool_result(
        tool_name="write_file", tool_call_id="write-result"
    )
    async with private_sessions() as writer:
        artifact = (
            await writer.scalars(
                select(ConversationArtifact).where(
                    ConversationArtifact.conversation_id == room_id,
                    ConversationArtifact.assistant_msg_id == str(run_id),
                )
            )
        ).one()
        artifact_id, first_version = artifact.id, artifact.current_version_id
        assert first_version is not None
        await enqueue_independent_completion(
            writer,
            job_id=job_id,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            conversation_id=room_id,
            run_id=run_id,
            artifact_id=artifact_id,
            version_id=first_version,
        )
        await writer.commit()
    async with private_sessions() as reader:
        receipt = (
            await reader.scalars(
                select(ChatNotificationOutbox).where(ChatNotificationOutbox.source_id == job_id)
            )
        ).one()
        assert receipt.status == "pending"
        outbox_id = receipt.id
        prior = Notification(
            org_id=ctx.org_id, tenant_id=ctx.tenant_id, user_id=ctx.user_id, kind="workflow.notify"
        )
        reader.add(prior)
        await reader.flush()
        await mark_read(reader, ctx, prior.id)
        await reader.commit()
        prior_id, prior_read = prior.id, prior.read_at
        assert prior_read is not None

    killed_pid, _ = await kill_after_notice_flush(lab, outbox_id, tmp_path)
    async with private_sessions() as reader:
        pending = await reader.get(ChatNotificationOutbox, outbox_id)
        assert pending and (pending.status, pending.attempt_count) == ("pending", 0)
    async with notification_poller(lab, tmp_path) as replacement:
        assert replacement.pid != killed_pid
        assert await asyncio.wait_for(replacement.wait(), timeout=45) == 0

    report.write_bytes(b"a later version which must not replace the completed result")
    assert await recorder.collect_after_tool_result(
        tool_name="edit_file", tool_call_id="rewrite-result"
    )
    async with private_sessions() as reader:
        current = await reader.get(ConversationArtifact, artifact_id)
        assert (
            current and current.current_version_id and current.current_version_id != first_version
        )
        receipt = (
            await reader.scalars(
                select(ChatNotificationOutbox).where(ChatNotificationOutbox.source_id == job_id)
            )
        ).one()
        assert receipt.status == "delivered" and receipt.notification_id is not None
        notice_id = receipt.notification_id
        destination = await open_chat_notification(reader, ctx, notice_id)
        assert (destination.job_id, destination.conversation_id, destination.run_id) == (
            job_id,
            room_id,
            run_id,
        )
        assert await get_chat_notification_result(reader, ctx, notice_id) == (original, report.name)
        assert (
            await reader.scalar(
                select(func.count(Notification.id)).where(Notification.target_id == outbox_id)
            )
            == 1
        )
        await mark_read(reader, ctx, notice_id)
        await reader.commit()
        notice = await reader.get(Notification, notice_id)
        assert notice and notice.read_at is not None
        read_at = notice.read_at

    async with notification_poller(lab, tmp_path) as replay:
        assert await asyncio.wait_for(replay.wait(), timeout=45) == 0
    assert not (tmp_path / "unexpected-notification-network").exists()
    async with private_sessions() as reader:
        assert (
            await reader.scalar(select(Notification.read_at).where(Notification.id == prior_id))
            == prior_read
        )
        [same] = (
            await reader.scalars(select(Notification).where(Notification.target_id == outbox_id))
        ).all()
        assert (same.id, same.read_at) == (notice_id, read_at)

    admin_engine = create_async_engine(async_url(lab.admin_url))
    try:
        async with async_sessionmaker(admin_engine, expire_on_commit=False)() as admin:
            company = await seed_company(admin, org_count=1)
        foreign = OrgContext(company.organizations[0], company.tenant_id, company.target_id)
        assert foreign.org_id != ctx.org_id and foreign.tenant_id != ctx.tenant_id
        async with tenant_session(foreign, maker=sessions) as outsider:
            for query in (open_chat_notification, get_chat_notification_result):
                with pytest.raises(AppError) as denied:
                    await query(outsider, foreign, notice_id)
                assert denied.value.status == 404
    finally:
        await admin_engine.dispose()

    async with private_sessions() as writer:
        await writer.execute(delete(Conversation).where(Conversation.id == parent_id))
        await writer.commit()
    async with private_sessions() as reader:
        with pytest.raises(AppError) as denied:
            await get_chat_notification_result(reader, ctx, notice_id)
        assert denied.value.status == 404
