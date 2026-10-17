"""Plan 8.8 checkpointer gate: block before the pool, not compare after the read (I-06).

The LangGraph checkpointer runs on its own psycopg pool outside Moldy RLS, so
company isolation for checkpoints is this gate plus the metadata cross-check
(``app.tenancy.checkpoint_audit``). ``conversation_checkpointer`` reads the
conversation row through the caller's (company-scoped) session and returns the
saver only when the conversation's company, organization and owner match the
context; any mismatch is the same 404 as a missing conversation.

Call sites that still use ``get_checkpointer()`` directly are pinned by
``tests/authz/test_i06_conversation_checkpointer.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import TenantScoped
from app.error_codes import conversation_not_found
from app.models.conversation import Conversation


def _thread_uuid(thread_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(thread_id)
    except ValueError:
        return None


async def require_conversation_thread(db: AsyncSession, thread_id: str, ctx: TenantScoped) -> None:
    """404 unless ``thread_id`` is a conversation of ``ctx``'s company (and org/owner)."""
    conversation_id = _thread_uuid(thread_id)
    row = None
    if conversation_id is not None:
        row = (
            await db.execute(
                select(Conversation.tenant_id, Conversation.org_id, Conversation.user_id)
                .where(Conversation.id == conversation_id)
                .execution_options(autoflush=False)
            )
        ).one_or_none()
    org_id = getattr(ctx, "org_id", None)
    if (
        row is None
        or row.tenant_id != ctx.tenant_id
        or (org_id is not None and row.org_id != org_id)
        or (ctx.user_id is not None and row.user_id != ctx.user_id)
    ):
        raise conversation_not_found()


async def conversation_checkpointer[S](
    thread_id: str,
    ctx: TenantScoped,
    *,
    db: AsyncSession,
    saver: Callable[[], S],
) -> S:
    """The checkpointer for one conversation thread, after the company gate.

    Callers pass their module's own ``get_checkpointer`` binding (the patch
    surface their tests use); it is only called once the gate passed.
    """
    await require_conversation_thread(db, thread_id, ctx)
    return saver()
