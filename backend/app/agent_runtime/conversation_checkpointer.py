"""Plan 8.8 checkpointer gate: block before the pool, not compare after the read (I-06).

The LangGraph checkpointer runs on its own psycopg pool outside Moldy RLS, so
company isolation for checkpoints is this gate plus the metadata cross-check
(``app.tenancy.checkpoint_audit``). ``conversation_checkpointer`` reads the
conversation row through the caller's (company-scoped) session and returns the
saver only when the conversation's company, organization and owner match the
context; any mismatch is the same 404 as a missing conversation.

``loaded_conversation_checkpointer`` is the same gate for callers that already
hold the row; ``thread_checkpointers`` covers builder and assistant threads.
Call sites that still use ``get_checkpointer()`` directly are pinned, with the
reason each one needs no gate, by ``tests/authz/test_i06_conversation_checkpointer.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

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


def require_row_scope(scope: tuple[object, object, object] | None, ctx: TenantScoped) -> None:
    """404 unless a conversation's ``(tenant_id, org_id, user_id)`` matches ``ctx``."""
    org_id = getattr(ctx, "org_id", None)
    if scope is None:
        raise conversation_not_found()
    tenant_id, row_org_id, user_id = scope
    if (
        tenant_id != ctx.tenant_id
        or (org_id is not None and row_org_id != org_id)
        or (ctx.user_id is not None and user_id != ctx.user_id)
    ):
        raise conversation_not_found()


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
    require_row_scope(None if row is None else tuple(row), ctx)


def default_saver() -> Any:
    """``checkpointer.get_checkpointer`` looked up at call time (its tests' patch surface)."""
    from app.agent_runtime import checkpointer

    return checkpointer.get_checkpointer()


def deferred_saver() -> Callable[[], Any]:
    """``default_saver`` itself, for callers that open the pool later (inside their own try)."""
    return default_saver


async def conversation_checkpointer[S](
    thread_id: str,
    ctx: TenantScoped,
    *,
    db: AsyncSession,
    saver: Callable[[], S] = default_saver,
) -> S:
    """The checkpointer for one conversation thread, after the company gate.

    Callers that bind ``get_checkpointer`` at module level pass that binding (the
    patch surface their tests use); it is only called once the gate passed.
    """
    await require_conversation_thread(db, thread_id, ctx)
    return saver()


async def loaded_conversation_checkpointer[S](
    conversation: Conversation,
    ctx: TenantScoped,
    *,
    db: AsyncSession | None,
    saver: Callable[[], S] = default_saver,
) -> S:
    """The same gate for callers holding the row; without a session the row is checked."""
    if db is not None:
        return await conversation_checkpointer(str(conversation.id), ctx, db=db, saver=saver)
    require_row_scope((conversation.tenant_id, conversation.org_id, conversation.user_id), ctx)
    return saver()
