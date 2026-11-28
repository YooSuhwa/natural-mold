"""Deliver a committed branch command or approval through its original conversation run."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.executor import execute_agent_stream_langgraph, resume_agent_stream_langgraph
from app.agent_runtime.runtime_config import AgentConfig
from app.approvals.emergency import refuse_run
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.conversation_scope import require_private_conversation, resolve_conversation_actor
from app.authz.independent_job_execution import authorize_independent_job_execution
from app.authz.org_context import OrgContext
from app.authz.run_decisions import fresh_run_decision_scope
from app.database import SessionTenant
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.user import User
from app.security.ag_ui_delivery_envelope import (
    DeliveryIdentity,
    InvalidDelivery,
    open_delivery,
    seal_delivery,
)
from app.services import chat_service
from app.services import conversation_run_queue_worker as queue_worker
from app.services.ag_ui_delivery_scope import DELIVERY_SOURCES, bound_deliveries
from app.services.conversation_run_queue_worker import (
    _company_of as queue_company_of,
)
from app.services.conversation_run_queue_worker import (
    _company_session as queue_company_session,
)
from app.services.conversation_run_queue_worker import (
    _session_factory as queue_sessions,
)
from app.services.conversation_run_queue_worker_metadata import (
    RUN_TERMINAL_OPTIONS,
    queue_current_user,
)
from app.services.conversation_run_state import transition_run
from app.services.conversation_run_task_registry import (
    RunTaskAlreadyRegisteredError,
    RunTaskRegistry,
)
from app.services.readiness_gate import abandons_run, require_agent_ready

logger = logging.getLogger(__name__)


async def seal_ag_ui_delivery(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    public_run_id: str,
    run_id: UUID,
    source: str,
    parent_run_id: UUID | None,
    checkpoint_id: str,
    payload: object,
) -> str:
    if (
        source not in DELIVERY_SOURCES
        or not checkpoint_id
        or (source == "resume") != (parent_run_id is not None)
    ):
        raise InvalidDelivery
    identity = DeliveryIdentity(
        context.tenant_id,
        context.org_id,
        context.user_id,
        conversation_id,
        public_run_id,
        run_id,
        source,
        parent_run_id,
        checkpoint_id,
    )
    return await seal_delivery(db, identity, payload)


@dataclass(slots=True)
class _Dispatch:
    run: ConversationRun
    user: CurrentUser
    cfg: AgentConfig
    input_payload: object
    attachment_ids: list[UUID]
    config_transform: Callable[[AgentConfig], AgentConfig] | None = None


async def _prepare(db: AsyncSession, run_id: UUID) -> _Dispatch | None:
    query = bound_deliveries().where(ConversationRun.id == run_id)
    row = (
        await db.execute(
            query.with_only_columns(
                Conversation.id,
                Conversation.user_id,
                Conversation.agent_id,
                ConversationRun.agent_id.label("accepted_agent_id"),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    if row.agent_id is None or row.agent_id != row.accepted_agent_id:
        raise InvalidDelivery
    context = await resolve_conversation_actor(db, row.id, row.user_id)
    await require_agent_run(context, row.agent_id)
    await chat_service.require_executable_conversation(db, row.id, context)
    conversation = await require_private_conversation(db, row.id, context)
    await refuse_run(db, tenant_id=context.tenant_id, agent_id=row.agent_id)
    await authorize_independent_job_execution(
        db, conversation_id=row.id, run_id=run_id, context=context
    )
    result = (
        await db.execute(
            query.add_columns(
                AgUiCommandReceipt.public_run_id,
                AgUiCommandReceipt.checkpoint_id,
                AgUiCommandReceipt.delivery_encrypted,
            )
        )
    ).one_or_none()
    if result is None:
        return None
    run, public_id, checkpoint_id, encrypted = result
    if not checkpoint_id or encrypted is None:
        raise InvalidDelivery
    identity = DeliveryIdentity(
        context.tenant_id,
        context.org_id,
        context.user_id,
        row.id,
        public_id,
        run_id,
        run.source,
        run.parent_run_id,
        checkpoint_id,
    )
    payload = await open_delivery(db, identity, encrypted)
    user = await db.get(User, context.user_id)
    if user is None or not user.is_active:
        raise InvalidDelivery
    current_user = queue_current_user(user)
    answers = payload.get("response") if isinstance(payload, dict) else None
    if (
        run.source != "resume"
        or not isinstance(answers, dict)
        or not abandons_run(answers.values())
    ):
        await require_agent_ready(db, context, row.agent_id)
    restricted = bool((run.metadata_json or {}).get("goal_followup_input_id"))
    cfg = await queue_worker.resolve_agent_context(
        db,
        row.id,
        current_user,
        context=context,
        checkpoint_id=checkpoint_id,
        include_subagents=not restricted,
    )
    if cfg.checkpoint_id != checkpoint_id:
        raise InvalidDelivery
    if run.source == "resume":
        from app.services.ag_ui_delivery_resume import (
            prepare_resume_delivery,
            resume_config_transform,
        )

        response = await prepare_resume_delivery(
            db,
            run=run,
            conversation=conversation,
            context=context,
            cfg=cfg,
            checkpoint_id=checkpoint_id,
            payload=payload,
        )
        return _Dispatch(run, current_user, cfg, response, [], resume_config_transform(cfg))

    from app.services.ag_ui_fork import prepare_fork_delivery

    fork_input, attachments = await prepare_fork_delivery(
        db,
        run=run,
        conversation=conversation,
        context=context,
        user=current_user,
        cfg=cfg,
        checkpoint_id=checkpoint_id,
        payload=payload,
    )
    return _Dispatch(run, current_user, cfg, fork_input, attachments)


async def _fail_before_start(scope: SessionTenant, run_id: UUID) -> ConversationRun | None:
    # A former member may no longer execute, but their exact unclaimed receipt must be closed.
    async with queue_company_session(scope) as db:
        run = await db.scalar(
            bound_deliveries()
            .where(ConversationRun.id == run_id)
            .with_for_update(of=ConversationRun)
            .options(*RUN_TERMINAL_OPTIONS)
        )
        if run is None:
            return None
        await transition_run(
            db,
            run,
            "failed",
            error_code="ag_ui_delivery_unavailable",
            error_message="The accepted command could not be resumed.",
        )
        await db.commit()
        return run


async def dispatch_ag_ui_run(
    run_id: UUID, *, registry: RunTaskRegistry | None = None
) -> ConversationRun | None:
    """Schedule this accepted run. The existing worker acquires the exclusive execution claim."""
    registry = registry or queue_worker.get_run_task_registry()
    scope = await queue_company_of(
        bound_deliveries()
        .with_only_columns(Conversation.tenant_id, Conversation.user_id)
        .where(ConversationRun.id == run_id)
    )
    if scope is None:
        return None
    try:
        with fresh_run_decision_scope():
            async with queue_company_session(scope) as db:
                prepared = await _prepare(db, run_id)
                if prepared is None:
                    return None
                await db.commit()
            run = prepared.run
            try:
                await queue_worker.start_conversation_run(
                    run_id=run.id,
                    conversation_id=run.conversation_id,
                    cfg=prepared.cfg,
                    user=prepared.user,
                    input_payload=prepared.input_payload,
                    moldy_source=run.source,
                    registry=registry,
                    attachment_ids=prepared.attachment_ids,
                    config_transform=prepared.config_transform,
                    executor_fn=(
                        resume_agent_stream_langgraph
                        if run.source == "resume"
                        else execute_agent_stream_langgraph
                    ),
                )
            except RunTaskAlreadyRegisteredError:
                if run_id not in registry.active_run_ids():
                    raise
            return run
    except (InvalidDelivery, ValueError):
        pass
    except (AppError, HTTPException) as error:
        status = error.status if isinstance(error, AppError) else error.status_code
        if status >= 500:
            raise
    return await _fail_before_start(scope, run_id)


async def recover_ag_ui_deliveries(*, registry: RunTaskRegistry | None = None) -> int:
    registry = registry or queue_worker.get_run_task_registry()
    async with queue_sessions()() as db:
        run_ids = list(
            (await db.scalars(bound_deliveries().with_only_columns(ConversationRun.id))).all()
        )
    launched = 0
    for run_id in run_ids:
        if run_id in registry.active_run_ids():
            continue
        try:
            run = await dispatch_ag_ui_run(run_id, registry=registry)
        except Exception:  # noqa: BLE001 - retry this receipt without blocking other companies
            logger.warning("Deferred AG-UI delivery will retry run_id=%s", run_id)
            continue
        if run is not None and run.status != "failed":
            launched += 1
    return launched
