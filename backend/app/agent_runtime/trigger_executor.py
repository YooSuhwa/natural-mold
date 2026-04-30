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
from app.models.model import Model
from app.services import chat_service

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


async def execute_trigger(trigger_id: str) -> None:
    """Called by APScheduler when a trigger fires."""

    trigger_uuid = uuid.UUID(trigger_id)

    async with async_session() as db:
        trigger = await db.get(AgentTrigger, trigger_uuid)
        if not trigger or trigger.status != "active":
            logger.info("Trigger %s skipped (not found or inactive)", trigger_id)
            return

        # Single source of truth for prefetch — same call the conversations
        # router uses, so no field on ``agent`` is lazy-loaded later.
        agent = await chat_service.get_agent_with_tools(db, trigger.agent_id, trigger.user_id)
        if not agent:
            logger.warning("Trigger %s: agent not found", trigger_id)
            trigger.status = "error"
            await db.commit()
            return

        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        conv = await chat_service.create_conversation(db, agent.id, title=f"자동 실행: {now_str}")

        effective_prompt = chat_service.build_effective_prompt(agent)
        tools_config = await chat_service.build_tools_config(agent, db=db)
        agent_skills = chat_service.build_agent_skills(agent)

        if agent.model is None:
            logger.warning(
                "Trigger %s: agent has no model bound — skipping run", trigger_id
            )
            return
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
            thread_id=str(conv.id),
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
        except Exception:
            logger.exception("Trigger %s: agent execution failed", trigger_id)

        trigger.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        trigger.run_count += 1
        await db.commit()

        logger.info(
            "Trigger %s executed successfully (run #%d)",
            trigger_id,
            trigger.run_count,
        )
