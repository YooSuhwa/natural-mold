"""Two different conversations cannot both acquire the same never-sent upload."""

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import tenant_session
from app.exceptions import AppError
from app.models.message_attachment import MessageAttachment
from app.services.ag_ui_start_effects import authorize_start_attachments
from app.services.chat.attachments import link_attachments_to_conversation
from tests.authz.execution_gateway_testkit import seed_graph
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as database:
        yield database


@pytest.mark.parametrize("commit_first", [True, False], ids=["first-commits", "first-rolls-back"])
async def test_postgres_reserves_attachment_until_admission_outcome(
    lab: RlsLab, monkeypatch: pytest.MonkeyPatch, commit_first: bool
) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    admin = create_async_engine(async_url(lab.admin_url))
    try:
        async with async_sessionmaker(admin, expire_on_commit=False)() as db:
            graph = await seed_graph(db)
            context = graph.context
            upload = MessageAttachment(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                filename="unclaimed.txt",
                mime_type="text/plain",
                size_bytes=1,
                storage_path="unused-upload-fixture",
                url="/unused",
            )
            db.add(upload)
            await db.commit()
            ids = [upload.id]
    finally:
        await admin.dispose()

    app = create_async_engine(async_url(lab.app_url))
    sessions = async_sessionmaker(app, expire_on_commit=False)
    first_id, second_id = graph.conversation_ids
    try:
        async with (
            tenant_session(context, maker=sessions) as first,
            tenant_session(context, maker=sessions) as second,
        ):
            holder = await first.scalar(text("SELECT pg_backend_pid()"))
            waiter = await second.scalar(text("SELECT pg_backend_pid()"))
            await authorize_start_attachments(first, context, first_id, ids)

            async def competing_admission() -> None:
                await authorize_start_attachments(second, context, second_id, ids)
                await link_attachments_to_conversation(
                    second, context=context, conversation_id=second_id, attachment_ids=ids
                )
                await second.commit()

            pending = asyncio.create_task(competing_admission())
            try:
                async with asyncio.timeout(10):
                    while not await first.scalar(
                        text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                        {"holder": holder, "waiter": waiter},
                    ):
                        if pending.done():
                            await pending
                            pytest.fail(
                                "The second conversation acquired the upload before release"
                            )
                        await asyncio.sleep(0.01)
                    await link_attachments_to_conversation(
                        first, context=context, conversation_id=first_id, attachment_ids=ids
                    )
                    if commit_first:
                        await first.commit()
                        with pytest.raises(AppError) as error:
                            await pending
                        assert (error.value.status, error.value.code) == (404, "FILE_NOT_FOUND")
                    else:
                        await first.rollback()
                        await pending
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        async with tenant_session(context, maker=sessions) as inspect:
            row = await inspect.get(MessageAttachment, ids[0])
            assert row is not None
            assert row.conversation_id == (first_id if commit_first else second_id)
    finally:
        await app.dispose()
