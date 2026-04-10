from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from app.agent_runtime.executor import AgentConfig, execute_agent_invoke
from app.database import async_session
from app.models.agent_trigger import AgentTrigger
from app.services import chat_service
from app.services.encryption import decrypt_api_key
from app.services.provider_service import load_all_provider_api_keys

logger = logging.getLogger(__name__)


async def execute_trigger(trigger_id: str) -> None:
    """Called by APScheduler when a trigger fires.

    Creates a new conversation, sends the trigger's input_message,
    runs the agent with tools, and stores the result.
    """
    trigger_uuid = uuid.UUID(trigger_id)

    async with async_session() as db:
        trigger = await db.get(AgentTrigger, trigger_uuid)
        if not trigger or trigger.status != "active":
            logger.info("Trigger %s skipped (not found or inactive)", trigger_id)
            return

        # Load agent with tools
        agent = await chat_service.get_agent_with_tools(db, trigger.agent_id, trigger.user_id)
        if not agent:
            logger.warning("Trigger %s: agent not found", trigger_id)
            trigger.status = "error"
            await db.commit()
            return

        # Create a new conversation for this trigger run
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        conv = await chat_service.create_conversation(db, agent.id, title=f"자동 실행: {now_str}")

        # Build prompt (with skill contents) and tools config via shared helpers
        effective_prompt = chat_service.build_effective_prompt(agent)
        tools_config = chat_service.build_tools_config(agent)
        agent_skills = chat_service.build_agent_skills(agent)

        # Build messages history
        messages_history = [{"role": "user", "content": trigger.input_message}]

        # Resolve API key: prefer llm_provider, fallback to model-level key
        lp = agent.model.llm_provider
        api_key = (
            decrypt_api_key(lp.api_key_encrypted)
            if lp and lp.api_key_encrypted
            else decrypt_api_key(agent.model.api_key_encrypted)
            if agent.model.api_key_encrypted
            else None
        )
        base_url = lp.base_url if lp and lp.base_url else agent.model.base_url

        provider_api_keys = await load_all_provider_api_keys(db)

        # Execute agent (non-streaming invoke)
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
            provider_api_keys=provider_api_keys,
        )
        try:
            await execute_agent_invoke(cfg, messages_history)
        except Exception:
            logger.exception("Trigger %s: agent execution failed", trigger_id)

        # Update trigger state
        trigger.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        trigger.run_count += 1
        await db.commit()

        logger.info(
            "Trigger %s executed successfully (run #%d)",
            trigger_id,
            trigger.run_count,
        )
