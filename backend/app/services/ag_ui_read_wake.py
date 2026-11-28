"""Check whether a scoped physical run has data worth another authorized read."""

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent, MessageEventChunk


async def committed_run_changed(
    db: AsyncSession,
    context: OrgContext,
    run: ConversationRun,
    generation: str | None,
    ordinal: int,
    trace_status: str | None,
) -> bool:
    """A polling hint contains no public data and never advances a read cursor."""
    trace, chunk = MessageEvent, MessageEventChunk
    has_new_chunk = exists(
        select(chunk.id).where(
            chunk.message_event_id == trace.id,
            chunk.conversation_id == run.conversation_id,
            chunk.assistant_msg_id == str(run.id),
            chunk.seq_end > ordinal,
        )
    )
    query = (
        select(
            ConversationRun.status,
            trace.id,
            trace.status,
            func.json_array_length(trace.events) > ordinal,
            has_new_chunk,
        )
        .select_from(ConversationRun)
        .outerjoin(
            trace,
            and_(
                trace.conversation_id == run.conversation_id, trace.assistant_msg_id == str(run.id)
            ),
        )
        .where(
            ConversationRun.id == run.id,
            ConversationRun.conversation_id == run.conversation_id,
            private_scope_predicate(ConversationRun, context),
        )
    )
    row = (await db.execute(query)).one_or_none()
    if row is None:
        return True
    status, record_id, stored_status, inline_advanced, appended = row
    return (
        status != run.status
        or (str(record_id) if record_id else None) != generation
        or stored_status != trace_status
        or bool(inline_advanced or appended)
    )
