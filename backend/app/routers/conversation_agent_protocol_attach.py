from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.event_broker import registry as broker_registry
from app.models.conversation_run import ConversationRun

_RUN_ATTACH_SETTLE_SECONDS = 1.0
_RUN_ATTACH_SETTLE_INTERVAL_SECONDS = 0.05


async def wait_for_live_broker_or_terminal(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> ConversationRun | None:
    deadline = asyncio.get_running_loop().time() + _RUN_ATTACH_SETTLE_SECONDS
    run: ConversationRun | None = None
    while asyncio.get_running_loop().time() < deadline:
        broker = broker_registry.get(str(run_id))
        if broker is not None and not broker.is_closed:
            return await db.get(ConversationRun, run_id)
        await asyncio.sleep(_RUN_ATTACH_SETTLE_INTERVAL_SECONDS)
        run = await db.get(ConversationRun, run_id)
        if run is None or not run.is_active:
            return run
        await db.refresh(run)
        if not run.is_active:
            return run
    return run or await db.get(ConversationRun, run_id)
