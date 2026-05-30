"""Scheduler entrypoint — executes a single trigger run.

Greenfield M5: pulls everything via ``chat_service.get_agent_with_tools`` so
prefetch matches the conversations router exactly. The legacy
``llm_provider`` join + Fernet decrypt is replaced by an optional
``Agent.llm_credential`` lookup; env-var fallback in ``model_factory`` covers
agents that haven't bound a credential yet.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.agent_runtime.executor import AgentConfig, execute_agent_invoke
from app.agent_runtime.model_factory import env_provider_keys
from app.database import async_session
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.model import Model
from app.services import chat_service, trigger_service

logger = logging.getLogger(__name__)


async def _resolve_fallback_chain(db, fallback_list):
    """Resolve ``Agent.model_fallback_list`` (UUID strings) into chain dicts.

    Mirrors the conversations router helper so trigger runs and chat use the
    same resolution rules: missing rows are silently dropped.
    """

    if not fallback_list:
        return None
    fallback_uuids: list[uuid.UUID] = []
    for raw in fallback_list:
        try:
            fallback_uuids.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not fallback_uuids:
        return None
    result = await db.execute(select(Model).where(Model.id.in_(fallback_uuids)))
    rows = {row.id: row for row in result.scalars().all()}
    chain = []
    for fid in fallback_uuids:
        row = rows.get(fid)
        if row is None:
            continue
        chain.append(
            {
                "provider": row.provider,
                "model_name": row.model_name,
                "base_url": row.base_url,
                "model_id": str(row.id),
            }
        )
    return chain or None


# ``_resolve_llm_api_key`` was inlined here; the shared resolver in
# ``app.agent_runtime.credential_resolution.resolve_llm_api_key_for_agent``
# now owns the policy (agent.llm_credential → model.default_credential_id →
# None).


async def execute_trigger(trigger_id: str, *, force: bool = False) -> AgentTriggerRun | None:
    """Called by APScheduler when a trigger fires."""

    trigger_uuid = uuid.UUID(trigger_id)

    async with async_session() as db:
        trigger = await db.get(AgentTrigger, trigger_uuid)
        if not trigger:
            logger.info("Trigger %s skipped (not found)", trigger_id)
            return

        run = await trigger_service.start_trigger_run(db, trigger)

        if trigger.status != "active" and not force:
            logger.info("Trigger %s skipped (inactive)", trigger_id)
            await trigger_service.finish_trigger_run(
                db,
                trigger=trigger,
                run=run,
                conversation=None,
                status="skipped",
                error_message="trigger is not active",
            )
            await db.refresh(run)
            return run

        # Single source of truth for prefetch — same call the conversations
        # router uses, so no field on ``agent`` is lazy-loaded later.
        agent = await chat_service.get_agent_with_tools(db, trigger.agent_id, trigger.user_id)
        if not agent:
            logger.warning("Trigger %s: agent not found", trigger_id)
            trigger.status = "error"
            await trigger_service.finish_trigger_run(
                db,
                trigger=trigger,
                run=run,
                conversation=None,
                status="failed",
                error_message="agent not found",
            )
            await db.refresh(run)
            return run

        conversation = await trigger_service.resolve_schedule_conversation(db, trigger)

        if agent.model is None:
            logger.warning(
                "Trigger %s: agent has no model bound — skipping run", trigger_id
            )
            await trigger_service.finish_trigger_run(
                db,
                trigger=trigger,
                run=run,
                conversation=conversation,
                status="failed",
                error_message="agent has no model bound",
            )
            await db.refresh(run)
            return run

        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        logger.info(
            "Trigger %s executing in conversation %s at %s",
            trigger_id,
            conversation.id,
            now_str,
        )

        effective_prompt = chat_service.build_effective_prompt(agent)
        tools_config = await chat_service.build_tools_config(
            agent, db=db, conversation_id=str(conversation.id)
        )
        agent_skills = chat_service.build_agent_skills(agent)

        api_key = await resolve_llm_api_key_for_agent(db, agent)
        base_url = agent.model.base_url

        fallback_chain = await _resolve_fallback_chain(db, agent.model_fallback_list)

        cfg = AgentConfig(
            provider=agent.model.provider,
            model_name=agent.model.model_name,
            api_key=api_key,
            base_url=base_url,
            system_prompt=effective_prompt,
            tools_config=tools_config,
            thread_id=str(conversation.id),
            model_params=agent.model_params,
            middleware_configs=agent.middleware_configs,
            agent_skills=agent_skills or None,
            agent_id=str(agent.id),
            provider_api_keys=env_provider_keys(),
            user_id=str(agent.user_id),
            model_id=str(agent.model.id) if agent.model else None,
            llm_credential_id=(
                str(agent.llm_credential.id) if agent.llm_credential is not None else None
            ),
            model_fallback_chain=fallback_chain,
        )
        try:
            await execute_agent_invoke(cfg, [{"role": "user", "content": trigger.input_message}])
        except Exception as exc:
            logger.exception("Trigger %s: agent execution failed", trigger_id)
            await trigger_service.finish_trigger_run(
                db,
                trigger=trigger,
                run=run,
                conversation=conversation,
                status="failed",
                error_message=str(exc),
            )
            await db.refresh(run)
            return run

        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=conversation,
            status="success",
        )
        await db.refresh(run)

        logger.info(
            "Trigger %s executed successfully (run #%d)",
            trigger_id,
            trigger.run_count,
        )
        return run
