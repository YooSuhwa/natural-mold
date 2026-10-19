from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.agent_runtime.runtime_config import AgentConfig
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.conversation_input_scope import conversation_input_parent
from app.authz.scoping import private_parent_scoped
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.user import User
from app.services import conversation_run_queue_worker_metadata as metadata
from app.services import conversation_run_service
from app.services.chat_resource_context_integration import (
    reauthorize_resource_context_payload,
)
from app.services.chat_resource_context_sources import ResourceContextScope
from app.services.conversation_run_worker import RunTaskAlreadyRegisteredError

logger = logging.getLogger("app.services.conversation_run_queue_worker")


def facade_attr(name: str) -> Any:
    """Resolve a patchable name on the queue worker facade at call time."""
    from app.services import conversation_run_queue_worker

    return getattr(conversation_run_queue_worker, name)


async def launch_claimed_input(input_id: uuid.UUID) -> ConversationRun | None:
    """Rehydrate and launch one claimed input without request-scoped state."""
    registry = facade_attr("get_run_task_registry")()
    cfg: AgentConfig | None = None
    error_code: str | None = None
    scope = await facade_attr("_input_company")(input_id)
    if scope is None:
        return None
    async with facade_attr("_company_session")(scope) as db:  # I-06: the input's company
        identity = await metadata.load_queue_input_identity(db, input_id)
        if identity is None or identity.run_id is None or identity.status != "claimed":
            return None
        run = await db.scalar(
            select(ConversationRun)
            .where(ConversationRun.id == identity.run_id)
            .options(*metadata.RUN_IDENTITY_OPTIONS)
            .execution_options(autoflush=False)
        )
        if run is None:
            return None
        try:
            target = await metadata.authorize_queue_target(db, identity, run)
            if identity.agent_id is None:
                raise metadata.QueueBindingInvalidError
            await load_agent_tree_for_execution(
                db, AgentExecutionTarget(identity.agent_id, target.context, run=True)
            )
        except metadata.QueueBindingInvalidError:
            await _fail_claimed_input(input_id, error_code="queue_binding_invalid")
            return None
        except AppError:
            is_active = await db.scalar(select(User.is_active).where(User.id == identity.user_id))
            error_code = (
                "queue_user_unavailable" if is_active is not True else "queue_rehydrate_failed"
            )
            await _fail_claimed_input(input_id, error_code=error_code)
            return run
        local_task = registry.get(target.run.id)
        if local_task is not None and not local_task.done():
            return target.run
        user = await db.get(User, identity.user_id)
        queued = await db.scalar(
            private_parent_scoped(
                select(ConversationRunInput).where(ConversationRunInput.id == input_id),
                conversation_input_parent(),
                target.context,
            )
        )
        if queued is None or user is None or queued.agent_id is None:
            await _fail_claimed_input(input_id, error_code="queue_rehydrate_failed")
            return run
        context = target.context
        current_user = metadata.queue_current_user(user)
        input_payload = queued.input_payload
        try:
            cfg = await facade_attr("resolve_agent_context")(
                db,
                queued.conversation_id,
                current_user,
                context=context,
                checkpoint_id=queued.checkpoint_id,
            )
        except (AppError, HTTPException, ValueError):
            logger.warning("queued input rehydration failed input_id=%s", input_id, exc_info=True)
            error_code = "queue_rehydrate_failed"
        if error_code is None:
            try:
                input_payload = await reauthorize_resource_context_payload(
                    db,
                    ResourceContextScope(
                        context=context,
                        agent_id=queued.agent_id,
                        conversation_id=queued.conversation_id,
                    ),
                    input_payload,
                )
            except HTTPException:
                logger.warning(
                    "queued resource context unavailable input_id=%s", input_id, exc_info=True
                )
                error_code = "queue_resource_context_unavailable"
        attachment_ids = [uuid.UUID(value) for value in queued.attachment_ids]
        conversation_id = queued.conversation_id
        run_id = run.id
        source = queued.source

    if error_code is not None:
        await _fail_claimed_input(input_id, error_code=error_code)
        return run
    if cfg is None:
        await _fail_claimed_input(input_id, error_code="queue_rehydrate_failed")
        return run

    local_task = registry.get(run_id)
    if local_task is not None and not local_task.done():
        return run
    try:
        await facade_attr("start_conversation_run")(
            run_id=run_id,
            conversation_id=conversation_id,
            cfg=cfg,
            user=current_user,
            input_payload=input_payload,
            moldy_source=source,
            executor_fn=facade_attr("execute_agent_stream_langgraph"),
            registry=registry,
            attachment_ids=attachment_ids,
        )
    except RunTaskAlreadyRegisteredError:
        local_task = registry.get(run_id)
        if local_task is None or local_task.done():
            raise
    return run


async def _fail_claimed_input(input_id: uuid.UUID, *, error_code: str) -> None:
    scope = await facade_attr("_input_company")(input_id)
    if scope is None:
        return
    async with facade_attr("_company_session")(scope) as db:  # I-06: the input's company
        identity = await metadata.load_queue_input_identity(db, input_id)
        if identity is None or identity.run_id is None:
            return
        conversation = (
            await db.execute(
                select(Conversation.id, Conversation.user_id, Conversation.agent_id)
                .where(Conversation.id == identity.conversation_id)
                .with_for_update(of=Conversation)
            )
        ).one_or_none()
        run = await db.scalar(
            select(ConversationRun)
            .where(ConversationRun.id == identity.run_id)
            .with_for_update(of=ConversationRun)
            .options(*metadata.RUN_TERMINAL_OPTIONS)
        )
        if conversation is None or run is None or run.status != "queued":
            return
        queued = await db.scalar(
            select(ConversationRunInput)
            .where(ConversationRunInput.id == input_id)
            .with_for_update(of=ConversationRunInput)
            .options(metadata.INPUT_TERMINAL_OPTIONS)
        )
        if queued is None or queued.run_id != run.id:
            return
        queued.status = "failed"
        queued.revision += 1
        if not metadata.queue_binding_matches(queued, run, conversation):
            await db.commit()
            return
        await conversation_run_service.transition_run(
            db,
            run,
            "failed",
            error_code=error_code,
            error_message="Queued input could not be reconstructed.",
        )
        await db.commit()
