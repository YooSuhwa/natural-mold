"""Service-side run-state hooks for prepared-lease materialization (Task 10, I15).

``agent_runtime`` must not import ``app.services``, so the transaction receives
these as injected callables: ``mark_prepared_running`` performs the
``queued -> running`` transition next to the marker pair, and
``fail_prepared_run`` durably fails a marked run whose lease could not be
activated or scheduled (run failed/inactive/completed, linked API/trigger
envelopes projected, linked claimed input failed). Both run on rows the caller
locked through the execution gateway.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import LockClass
from app.authz.organization_execution_gateway import lock_many
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services.conversation_run_state import transition_run

_MESSAGE = "Prepared run could not be started"


def mark_prepared_running(
    worker_instance_id: str,
) -> Callable[[AsyncSession, ConversationRun], Awaitable[None]]:
    async def mark(db: AsyncSession, run: ConversationRun) -> None:
        await transition_run(db, run, "running", worker_instance_id=worker_instance_id)

    return mark


async def fail_prepared_run(db: AsyncSession, run: ConversationRun, code: str) -> None:
    inputs = await lock_many(
        db,
        LockClass.INPUT,
        select(ConversationRunInput)
        .where(
            ConversationRunInput.run_id == run.id,
            ConversationRunInput.status.in_(("pending", "claimed")),
        )
        .with_for_update(),
    )
    for item in inputs:
        item.status = "failed"
    await transition_run(db, run, "failed", error_code=code, error_message=_MESSAGE)
