"""Trigger execution block 3: runtime config preparation, acceptance and invoke."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.identity import AgentIdentityError
from app.agent_runtime.run_error_codes import denial_error_code
from app.agent_runtime.run_secrets import collect_cfg_secret_values
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.trigger_agent_config import (
    TriggerConfigSource,
    build_trigger_agent_config,
)
from app.agent_runtime.trigger_execution_materialization import (
    TriggerMaterialized,
    TriggerOutcome,
)
from app.config import settings
from app.database import tenant_session
from app.exceptions import AppError
from app.models.agent import Agent
from app.services import trigger_service

logger = logging.getLogger("app.agent_runtime.trigger_executor")


def _executor_attr(name: str) -> Any:
    """Return ``trigger_executor.<name>`` as currently bound (tests patch it)."""

    from app.agent_runtime import trigger_executor

    return getattr(trigger_executor, name)


async def _configure_legacy_children(db: AsyncSession, agent: Agent, cfg: AgentConfig) -> None:
    await _executor_attr("_configure_trigger_subagents")(
        _executor_attr("_TriggerSubagentBuild")(agent, db, cfg)
    )
    # ADR-021 H1 — populate the eager secret set (subagents resolved above)
    # so value-based redaction masks trigger-run trace persistence too.
    # ``.update`` preserves the subagent secrets already unioned above.
    cfg.secret_values.update(collect_cfg_secret_values(cfg))


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
    fallback_chain = await _executor_attr("_resolve_fallback_chain")(db, agent.model_fallback_list)
    cfg = build_trigger_agent_config(
        TriggerConfigSource(
            agent=agent,
            context=context,
            identity=identity,
            effective_prompt=effective_prompt,
            agent_skills=agent_skills,
            tools_config=tools_config,
            thread_id=str(conversation.id),
            runtime_policy=runtime_policy,
            api_key=api_key,
            fallback_chain=fallback_chain,
        )
    )
    await db.flush()
    # Task 10 I18: a prepared trigger resolves children under the materialization
    # locks; the legacy child config is deferred to the unsupported fallback.
    prepared = settings.prepared_run_lease_enabled
    if not prepared:
        try:
            await _configure_legacy_children(db, agent, cfg)
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

    ownership = trigger_service.RuntimeOwnershipRequest(
        run_id=accepted.run_id,
        conversation_id=conversation.id,
        root_agent_id=agent.id,
        context=context,
    )

    async def legacy_fallback() -> str:
        async with tenant_session(context, maker=_executor_attr("async_session")) as child_db:
            await _configure_legacy_children(child_db, agent, cfg)
        return await invoke()

    try:
        if prepared:
            # Task 10 I18: lock, revalidate, materialize and dispatch the lease;
            # unsupported shapes fall back to the legacy invoke before the marker.
            target = trigger_service.PreparedTriggerTarget(
                trigger.id, run.id, run.source == "run_now", ownership
            )
            output = await trigger_service.run_prepared_trigger_execution(target, legacy_fallback)
        else:
            output = await trigger_service.run_owned_execution(ownership, invoke)
    except trigger_service.PreparedTriggerTerminalError as exc:
        logger.info("Trigger %s %s before activation", trigger_id, exc.status)
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=conversation,
            status=exc.status,
            error_message=exc.message,
        )
        await db.refresh(run)
        return TriggerOutcome(run)
    except AppError as exc:
        if prepared and isinstance(exc, AgentIdentityError):
            # Flag on: the legacy fallback configures children after the claim, so
            # the identity policy error arrives here; keep the legacy outcome (M3).
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
        else:
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
            # A mid-run lease denial records its shared code, like the run (M2).
            error_message=denial_error_code(exc) or str(exc),
            thread_id=str(conversation.id),
        )
        await db.refresh(run)
        return TriggerOutcome(run)
    return TriggerInvoked(output)
