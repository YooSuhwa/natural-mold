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

from app.agent_runtime.executor import AgentConfig, execute_agent_invoke
from app.agent_runtime.model_factory import env_provider_keys
from app.credentials import service as credential_service
from app.database import async_session
from app.models.agent_trigger import AgentTrigger
from app.services import chat_service

logger = logging.getLogger(__name__)


async def _resolve_llm_api_key(agent) -> str | None:
    """Decrypt ``Agent.llm_credential`` and return ``api_key``.

    Returns ``None`` when no credential is bound (env-var fallback handles it).
    """

    cred = getattr(agent, "llm_credential", None)
    if cred is None:
        return None
    try:
        payload = await credential_service.decrypt_with_external(cred.data_encrypted)
    except Exception:  # noqa: BLE001 — surface as missing key
        logger.exception("LLM credential %s decryption failed", cred.id)
        return None
    api_key = payload.get("api_key") or payload.get("token")
    return str(api_key) if api_key else None


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

        api_key = await _resolve_llm_api_key(agent)
        base_url = agent.model.base_url

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
