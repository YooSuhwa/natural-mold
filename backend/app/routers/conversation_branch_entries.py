"""Edit/regenerate/switch-branch entry bodies behind ``conversation_branches`` routes."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.error_codes import conversation_not_found
from app.models.conversation import Conversation
from app.routers.chat_run_entries import _broker_resume_generator
from app.schemas.conversation import (
    EditMessageRequest,
    RegenerateMessageRequest,
    SwitchBranchRequest,
)
from app.schemas.conversation_scope import ConversationBranchMutation
from app.services import chat_service
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_branch_service import (
    active_checkpoint_from_override,
    find_message_in_checkpoints,
    resolve_branch_checkpoint,
    with_regeneration_guidance,
)
from app.services.conversation_run_worker import start_conversation_run
from app.services.conversation_stream_service import resolve_agent_context, sse_response


def _branches_attr(name: str) -> Any:
    """Resolve a patchable name through the ``conversation_branches`` facade at call time."""
    from app.routers import conversation_branches

    return getattr(conversation_branches, name)


async def edit_message(
    *,
    conversation_id: uuid.UUID,
    data: EditMessageRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    context: OrgContext,
) -> StreamingResponse:
    agent_id = await chat_service.prepare_executable_conversation(db, conversation_id, context)
    conv = await chat_service.get_owned_conversation_with_agent(db, conversation_id, context)
    if conv is None:
        raise conversation_not_found()
    resolved = await resolve_branch_checkpoint(
        conversation_id, data.message_id, db=db, context=context
    )
    if not resolved.found:
        raise HTTPException(status_code=422, detail="message does not belong to this conversation.")
    checkpoint_id = resolved.checkpoint_id
    from langchain_core.messages import HumanMessage

    from app.services.thread_branch_service import build_fork_overwrite_input

    overwrite_input = await build_fork_overwrite_input(
        await conversation_checkpointer(str(conversation_id), context, db=db),
        str(conversation_id),
        checkpoint_id,
        append=[HumanMessage(content=data.new_content)],
    )
    cfg = await resolve_agent_context(
        db, conversation_id, user, context=context, checkpoint_id=checkpoint_id
    )
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation_id,
            root_agent_id=agent_id,
            context=context,
            source="chat",
            run_source="edit",
            input_preview=data.new_content,
        ),
    )
    await chat_service.touch_conversation(db, conversation_id, context)
    await chat_service.clear_active_branch_override(db, conversation_id, context)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_edit",
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conv),
        metadata={
            "message_id": str(data.message_id),
            "new_content_length": len(data.new_content),
        },
    )
    run_id = accepted.run_id
    await db.commit()

    ctx = await start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        input_payload=overwrite_input,
        moldy_source="edit",
        executor_fn=_branches_attr("execute_agent_stream"),
    )
    return sse_response(
        _broker_resume_generator(ctx.broker, None),
        extra_headers={"X-Run-Id": ctx.run_id},
    )


async def regenerate_message(
    *,
    conversation_id: uuid.UUID,
    data: RegenerateMessageRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    context: OrgContext,
    conv: Conversation,
) -> StreamingResponse:
    agent_id = await chat_service.prepare_executable_conversation(db, conversation_id, context)
    from app.services.thread_branch_service import _collect_checkpoints  # noqa: PLC2701

    checkpointer = await conversation_checkpointer(str(conversation_id), context, db=db)
    checkpoints = await _collect_checkpoints(checkpointer, str(conversation_id))
    if not checkpoints:
        raise HTTPException(status_code=422, detail="conversation has no history yet.")

    target_msg = None
    target_idx: int | None = None
    if data.message_id is None:
        active = active_checkpoint_from_override(
            checkpoints,
            conv.active_branch_checkpoint_id,
        )
        msgs = active.messages
        for i in range(len(msgs) - 1, -1, -1):
            if getattr(msgs[i], "type", None) == "ai":
                target_idx = i
                target_msg = msgs[i]
                break
    else:
        found = find_message_in_checkpoints(
            checkpoints,
            conversation_id,
            data.message_id,
        )
        if found is not None:
            target_msg, target_idx = found
            if getattr(target_msg, "type", None) != "ai":
                raise HTTPException(
                    status_code=422,
                    detail="can only regenerate assistant messages.",
                )

    if target_idx is None or target_idx == 0:
        raise HTTPException(status_code=422, detail="cannot regenerate — no parent user message.")

    from app.services.thread_branch_service import (
        build_fork_overwrite_input,
        rewind_to_checkpoint_before_message,
    )

    target_msg_raw = getattr(target_msg, "id", None) or f"synthetic-{target_idx}"
    checkpoint_id = await rewind_to_checkpoint_before_message(
        checkpointer, str(conversation_id), target_msg_raw
    )

    overwrite_input = await build_fork_overwrite_input(
        checkpointer, str(conversation_id), checkpoint_id
    )
    cfg = await resolve_agent_context(
        db, conversation_id, user, context=context, checkpoint_id=checkpoint_id
    )

    def guidance(resolved: AgentConfig) -> AgentConfig:
        return with_regeneration_guidance(resolved, target_msg)

    cfg = guidance(cfg)
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation_id,
            root_agent_id=agent_id,
            context=context,
            source="chat",
            run_source="regenerate",
            input_preview=None,
        ),
    )
    await chat_service.touch_conversation(db, conversation_id, context)
    await chat_service.clear_active_branch_override(db, conversation_id, context)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_regenerate",
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conv),
        metadata={
            "message_id": str(data.message_id) if data.message_id else None,
        },
    )
    run_id = accepted.run_id
    await db.commit()

    ctx = await start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        input_payload=overwrite_input,
        moldy_source="regenerate",
        executor_fn=_branches_attr("execute_agent_stream"),
        config_transform=guidance,
    )
    return sse_response(
        _broker_resume_generator(ctx.broker, None),
        extra_headers={"X-Run-Id": ctx.run_id},
    )


async def switch_branch(
    *,
    conversation_id: uuid.UUID,
    data: SwitchBranchRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    conv: Conversation,
    context: OrgContext,
) -> None:
    await chat_service.require_executable_conversation(db, conversation_id, context)
    from app.services.thread_branch_service import checkpoint_exists

    if not await checkpoint_exists(
        await conversation_checkpointer(str(conversation_id), context, db=db),
        str(conversation_id),
        data.checkpoint_id,
    ):
        raise HTTPException(
            status_code=422,
            detail="checkpoint does not belong to this conversation.",
        )

    await chat_service.set_active_branch(
        db, ConversationBranchMutation(conversation_id, context, data.checkpoint_id)
    )
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.switch_branch",
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conv),
        title=conv.title,
        metadata={"checkpoint_id": data.checkpoint_id},
    )
    await db.commit()
