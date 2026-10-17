"""Queued input refused at dispatch (plan 11.6; F-08).

The queue worker re-decides the caller's ``agent#can_run`` before every claim
(D-01). A definite denial pauses the conversation queue (the inputs stay
``pending``; no run exists yet), audits ``run.blocked`` with the 11.6 reason and
commits, so the periodic queue recovery (which skips paused queues) does not
re-decide and re-audit it every tick. Resuming the queue re-checks ``can_run``.
``authz_unavailable`` is transient: nothing is written, the input stays pending and
the next dispatch decides again.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.dispatch_block import TRANSIENT, block_reason
from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.conversation_run import utc_now_naive
from app.services.conversation_run_queue_mutations import lock_owned_conversation
from app.services.run_block_service import record_run_blocked


async def pause_blocked_queue(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    context: OrgContext,
    denial: BaseException,
) -> None:
    reason = block_reason(denial)
    if reason is None or reason in TRANSIENT:
        return
    conversation = await lock_owned_conversation(
        db, conversation_id=conversation_id, context=context
    )
    if conversation.queue_paused:
        return
    conversation.queue_paused = True
    conversation.queue_paused_at = utc_now_naive()
    code = getattr(denial, "code", None)
    await record_run_blocked(
        db,
        invoker="queue",
        reason=reason,
        agent_id=agent_id,
        owner_user_id=context.user_id,
        agent_owner_user_id=await db.scalar(select(Agent.user_id).where(Agent.id == agent_id)),
        code=code if isinstance(code, str) else None,
        source_id=conversation_id,
    )
    await db.commit()


__all__ = ["pause_blocked_queue"]
