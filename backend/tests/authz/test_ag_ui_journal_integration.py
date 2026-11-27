"""A detached reader advances only across contiguous committed PostgreSQL trace writes."""

from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent_runtime.protocol_events import stored_protocol_event
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.models import Conversation
from app.models.message_event import MessageEventChunk
from app.services.ag_ui_journal import JournalPage, read_journal_page
from app.services.trace_storage import append_events
from tests.authz.i06_rls_lab import async_url, rls_lab
from tests.authz.test_goal_rls_integration import _actor, _conversation  # noqa: PLC2701

pytestmark = pytest.mark.integration


async def _append(writer: AsyncSession, conversation_id: UUID, run_id: UUID, seq: int) -> str:
    event = stored_protocol_event(
        run_id=str(run_id),
        thread_id=str(conversation_id),
        seq=seq,
        method="messages",
        data={"type": "ai", "id": f"answer-{seq}", "content": "Hello"},
    )
    await append_events(
        writer,
        conversation_id=conversation_id,
        assistant_msg_id=str(run_id),
        events_chunk=[dict(event)],
    )
    await writer.flush()
    return event["id"]


async def test_journal_advances_only_on_committed_contiguous_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    with rls_lab() as lab:
        with lab.admin.begin() as seed:
            actor = _actor(seed, lab.tenant_a)
            own = _conversation(seed, actor)
            sibling = _conversation(seed, actor)
        context = OrgContext(own.org, own.tenant, own.owner)
        url = async_url(lab.app_url)
        writes = create_async_engine(url)
        reads = create_async_engine(
            url, connect_args={"server_settings": {"default_transaction_read_only": "on"}}
        )
        try:
            async with (
                tenant_session(context, maker=async_sessionmaker(writes)) as writer,
                tenant_session(context, maker=async_sessionmaker(reads)) as reader,
            ):
                writer_pid = await writer.scalar(sa.text("SELECT pg_backend_pid()"))
                reader_pid = await reader.scalar(sa.text("SELECT pg_backend_pid()"))
                assert writer_pid != reader_pid
                assert await reader.scalar(sa.text("SHOW transaction_read_only")) == "on"

                conversation = await reader.get(Conversation, own.conversation)
                other = await reader.get(Conversation, sibling.conversation)
                assert conversation is not None and other is not None

                async def page(after: int = 0) -> JournalPage:
                    return await read_journal_page(
                        reader, conversation=conversation, physical_run_id=own.run, after=after
                    )

                first_id = await _append(writer, own.conversation, own.run, 10)
                assert await page() == JournalPage(None, (), False, False, None)
                await writer.commit()
                first = await page()
                assert first.generation and first.trace_status == "streaming"
                assert not first.gap and not first.has_more
                assert [(e.ordinal, e.source_id) for e in first.entries] == [(1, first_id)]

                caught_up = JournalPage(first.generation, (), False, False, "streaming")
                await _append(writer, own.conversation, own.run, 20)
                assert await page(1) == caught_up
                await writer.rollback()
                assert await page(1) == caught_up
                assert (await page(2)).gap

                second_id = await _append(writer, own.conversation, own.run, 30)
                await writer.commit()
                second = await page(1)
                assert second.generation == first.generation and not second.gap
                assert [(e.ordinal, e.source_id) for e in second.entries] == [(2, second_id)]
                assert await read_journal_page(
                    reader, conversation=other, physical_run_id=own.run
                ) == JournalPage(None, (), False, False, None)

                await _append(writer, own.conversation, own.run, 40)
                await writer.commit()
                # A retained tail beyond a missing chunk cannot be sent as a continuation.
                with lab.admin.begin() as admin:
                    deleted = admin.execute(
                        sa.delete(MessageEventChunk).where(
                            MessageEventChunk.conversation_id == own.conversation,
                            MessageEventChunk.assistant_msg_id == str(own.run),
                            MessageEventChunk.seq_start == 2,
                        )
                    )
                    assert deleted.rowcount == 1
                assert await page(1) == JournalPage(first.generation, (), False, True, "streaming")
        finally:
            await reads.dispose()
            await writes.dispose()
