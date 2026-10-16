"""Trigger execution block 4: terminal success delivery."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.trigger_execution_materialization import TriggerMaterialized
from app.models.agent_trigger_run import AgentTriggerRun
from app.services import trigger_service

logger = logging.getLogger("app.agent_runtime.trigger_executor")


async def deliver_trigger_success(
    db: AsyncSession, trigger_id: str, materialized: TriggerMaterialized, output: Any
) -> AgentTriggerRun:
    trigger, run, conversation = materialized.trigger, materialized.run, materialized.conversation

    await trigger_service.finish_trigger_run(
        db,
        trigger=trigger,
        run=run,
        conversation=conversation,
        status="success",
        output_preview=str(output) if output is not None else None,
        thread_id=str(conversation.id),
    )
    await db.refresh(run)

    logger.info(
        "Trigger %s executed successfully (run #%d)",
        trigger_id,
        trigger.run_count,
    )
    return run
