"""Trigger execution block 1: row lock, authorization revalidation and claim."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.org_context import OrgContext
from app.authz.trigger_scope import require_trigger_actor
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.services import chat_service, trigger_service

logger = logging.getLogger("app.agent_runtime.trigger_executor")


@dataclass(slots=True)
class TriggerClaim:
    trigger: AgentTrigger
    context: OrgContext
    agent: Agent
    blocked_tools: list[Any]
    tools_config: list[dict[str, Any]]
    run: AgentTriggerRun


async def claim_trigger_run(
    db: AsyncSession, trigger_id: str, trigger_uuid: uuid.UUID, *, force: bool
) -> TriggerClaim | None:
    # SEC-3 duplicate guard: lock the trigger row so concurrent entries
    # (scheduled fire vs user run-now, which bypasses APScheduler's
    # max_instances) serialise, then claim by checking for an in-flight
    # run before creating ours. FOR UPDATE is a no-op on SQLite (tests
    # exercise the check itself); on Postgres it closes the
    # check-then-insert race. The lock remains held through durable run
    # acceptance, so a contender re-reads only after the envelope is visible.
    trigger = await db.scalar(select(AgentTrigger).where(AgentTrigger.id == trigger_uuid))
    if not trigger:
        logger.info("Trigger %s skipped (not found)", trigger_id)
        return None

    try:
        context = await require_trigger_actor(db, trigger)
        await load_agent_tree_for_execution(db, AgentExecutionTarget(trigger.agent_id, context))
        trigger = await db.scalar(
            select(AgentTrigger)
            .where(AgentTrigger.id == trigger_uuid, AgentTrigger.agent_id == trigger.agent_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if trigger is None:
            return None
        agent = await chat_service.get_agent_with_tools(db, trigger.agent_id, context)
        if agent is None:
            return None
        blocked_tools = await chat_service.trigger_blocked_tools_for_agent_tree(
            agent, context=context, db=db
        )
        tools_config = await chat_service.build_tools_config(
            agent, context=context, db=db, conversation_id=None
        )
    except AppError as exc:
        logger.info("Trigger %s skipped (authorization: %s)", trigger_id, exc.code)
        return None

    in_flight = await trigger_service.find_in_flight_run(db, trigger_uuid)
    if in_flight is not None:
        logger.info("Trigger %s skipped (run %s already in flight)", trigger_id, in_flight.id)
        if force:
            raise trigger_service.TriggerRunInFlightError(in_flight.id)
        return None

    run = await trigger_service.start_trigger_run(
        db,
        trigger,
        source="run_now" if force else "scheduled",
    )
    return TriggerClaim(trigger, context, agent, blocked_tools, tools_config, run)
