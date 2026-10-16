"""Trigger execution block 3: runtime config preparation, acceptance and invoke."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.identity import AgentIdentityError
from app.agent_runtime.run_secrets import collect_cfg_secret_values
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.trigger_execution_materialization import (
    TriggerMaterialized,
    TriggerOutcome,
)
from app.exceptions import AppError
from app.services import trigger_service

logger = logging.getLogger("app.agent_runtime.trigger_executor")


def _executor_attr(name: str) -> Any:
    """Return ``trigger_executor.<name>`` as currently bound (tests patch it)."""

    from app.agent_runtime import trigger_executor

    return getattr(trigger_executor, name)


@dataclass(frozen=True, slots=True)
class TriggerInvoked:
    output: Any


async def invoke_trigger_run(
    db: AsyncSession, trigger_id: str, materialized: TriggerMaterialized
) -> TriggerInvoked | TriggerOutcome:
    trigger, run, agent = materialized.trigger, materialized.run, materialized.agent
    context, identity = materialized.context, materialized.identity
    effective_prompt, agent_skills = materialized.effective_prompt, materialized.agent_skills
    tools_config, conversation = materialized.tools_config, materialized.conversation
    runtime_policy = materialized.runtime_policy

    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    logger.info(
        "Trigger %s executing in conversation %s at %s",
        trigger_id,
        conversation.id,
        now_str,
    )

    api_key = await _executor_attr("resolve_llm_api_key_for_agent")(
        db, agent, AgentCredentialAccess(context, identity)
    )
    base_url = agent.model.base_url

    fallback_chain = await _executor_attr("_resolve_fallback_chain")(db, agent.model_fallback_list)
    provider_api_keys: dict[str, str | None] | None = (
        {agent.model.provider: api_key} if api_key else None
    )

    cfg = AgentConfig(
        org_context=context,
        provider=agent.model.provider,
        model_name=agent.model.model_name,
        api_key=api_key,
        base_url=base_url,
        system_prompt=effective_prompt,
        tools_config=tools_config,
        thread_id=str(conversation.id),
        runtime_policy=runtime_policy,
        model_params=agent.model_params,
        middleware_configs=agent.middleware_configs,
        agent_skills=agent_skills or None,
        agent_id=str(agent.id),
        agent_name=agent.name,
        provider_api_keys=provider_api_keys,
        context_window=agent.model.context_window,
        user_id=str(agent.user_id),
        model_id=str(agent.model.id) if agent.model else None,
        llm_credential_id=(
            str(agent.llm_credential.id) if agent.llm_credential is not None else None
        ),
        model_fallback_chain=fallback_chain,
        agent_owner_user_id=str(agent.user_id),
        caller_user_id=None,
        credential_subject_user_id=str(identity.credential_subject_user_id),
        identity_mode=identity.identity_mode,
        agent_runtime_name=identity.runtime_name,
    )
    await db.flush()
    try:
        await _executor_attr("_configure_trigger_subagents")(
            _executor_attr("_TriggerSubagentBuild")(agent, db, cfg)
        )
    except AgentIdentityError as exc:
        logger.warning("Trigger %s blocked by subagent identity policy: %s", trigger_id, exc)
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=conversation,
            status="failed",
            error_message=str(exc),
            thread_id=str(conversation.id),
        )
        await db.refresh(run)
        return TriggerOutcome(run)
    # ADR-021 H1 — populate the eager secret set (subagents resolved above)
    # so value-based redaction masks trigger-run trace persistence too.
    # ``.update`` preserves the subagent secrets already unioned above.
    cfg.secret_values.update(collect_cfg_secret_values(cfg))
    run.conversation_id = conversation.id
    await db.flush()
    accepted = await trigger_service.accept_owned_execution(
        db,
        trigger_service.OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=agent.id,
            context=context,
            source="trigger",
            input_preview=trigger.input_message,
            envelope=trigger_service.ExecutionEnvelopeRef("trigger", run.id),
        ),
    )
    await db.commit()

    async def invoke() -> str:
        return await _executor_attr("_invoke_trigger")(
            _executor_attr("_TriggerInvocation")(cfg, trigger.input_message, accepted.run_id)
        )

    try:
        output = await trigger_service.run_owned_execution(
            trigger_service.RuntimeOwnershipRequest(
                run_id=accepted.run_id,
                conversation_id=conversation.id,
                root_agent_id=agent.id,
                context=context,
            ),
            invoke,
        )
    except AppError as exc:
        logger.info("Trigger %s stopped before invoke: %s", trigger_id, exc.code)
        await db.refresh(run)
        return TriggerOutcome(run)
    except Exception as exc:
        logger.exception("Trigger %s: agent execution failed", trigger_id)
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=conversation,
            status="failed",
            error_message=str(exc),
            thread_id=str(conversation.id),
        )
        await db.refresh(run)
        return TriggerOutcome(run)
    return TriggerInvoked(output)
