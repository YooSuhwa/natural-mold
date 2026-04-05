from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from app.agent_runtime.executor import execute_agent_stream
from app.database import async_session
from app.models.agent_trigger import AgentTrigger
from app.services import chat_service

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

        # Execute agent (consume full stream, non-streaming)
        full_content = ""
        try:
            async for chunk in execute_agent_stream(
                provider=agent.model.provider,
                model_name=agent.model.model_name,
                api_key=agent.model.api_key_encrypted,
                base_url=agent.model.base_url,
                system_prompt=effective_prompt,
                tools_config=tools_config,
                messages_history=messages_history,
                thread_id=str(conv.id),
                model_params=agent.model_params,
                middleware_configs=agent.middleware_configs,
                agent_skills=agent_skills or None,
                agent_id=str(agent.id),
            ):
                # Parse SSE events to extract content
                for line in chunk.strip().split("\n"):
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if "delta" in data:
                                full_content += data["delta"]
                            elif "content" in data and not full_content:
                                full_content = data["content"]
                        except json.JSONDecodeError:
                            pass
        except Exception:
            logger.exception("Trigger %s: agent execution failed", trigger_id)
            full_content = "트리거 실행 중 오류가 발생했습니다."

        # Update trigger state
        trigger.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        trigger.run_count += 1
        await db.commit()

        logger.info(
            "Trigger %s executed successfully (run #%d)",
            trigger_id,
            trigger.run_count,
        )
