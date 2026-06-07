from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import conversation_not_found
from app.schemas.conversation import (
    EditMessageRequest,
    RegenerateMessageRequest,
    SwitchBranchRequest,
)
from app.services import chat_service
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_branch_service import (
    active_checkpoint_from_override,
    find_message_in_checkpoints,
    resolve_branch_checkpoint,
    with_regeneration_guidance,
)
from app.services.conversation_stream_service import (
    build_artifact_recorder,
    execute_agent_stream,
    prepare_stream_context,
    resolve_agent_context,
    sse_handler,
)

router = APIRouter(tags=["conversations"])


@router.post("/api/conversations/{conversation_id}/messages/edit")
async def edit_message(
    conversation_id: uuid.UUID,
    data: EditMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    resolved = await resolve_branch_checkpoint(conversation_id, data.message_id)
    if not resolved.found:
        raise HTTPException(status_code=422, detail="message does not belong to this conversation.")
    checkpoint_id = resolved.checkpoint_id
    cfg = await resolve_agent_context(db, conversation_id, user, checkpoint_id=checkpoint_id)
    await chat_service.touch_conversation(db, conversation_id)
    await chat_service.clear_active_branch_override(db, conversation_id)

    from langchain_core.messages import HumanMessage

    from app.agent_runtime.checkpointer import get_checkpointer
    from app.services.thread_branch_service import build_fork_overwrite_input

    overwrite_input = await build_fork_overwrite_input(
        get_checkpointer(),
        str(conversation_id),
        checkpoint_id,
        append=[HumanMessage(content=data.new_content)],
    )
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_edit",
        conversation_id=conversation_id,
        agent_id=uuid.UUID(cfg.agent_id) if cfg.agent_id else None,
        metadata={
            "message_id": str(data.message_id),
            "new_content_length": len(data.new_content),
        },
    )
    await db.commit()

    ctx = prepare_stream_context(conversation_id)
    stream_kwargs = ctx.as_stream_kwargs()
    stream_kwargs["artifact_recorder"] = build_artifact_recorder(
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        run_id=ctx.run_id,
    )
    return sse_handler(
        lambda: execute_agent_stream(
            cfg,
            overwrite_input,
            moldy_source="edit",
            **stream_kwargs,
        ),
        log_msg=f"Agent edit failed for conversation {conversation_id}",
        user_msg="메시지 편집 중 오류가 발생했습니다.",
        run_id=ctx.run_id,
        on_complete=ctx.finalize_callback(conversation_id),
        failure_probe=ctx.has_stream_error,
    )


@router.post("/api/conversations/{conversation_id}/messages/regenerate")
async def regenerate_message(
    conversation_id: uuid.UUID,
    data: RegenerateMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.services.thread_branch_service import _collect_checkpoints  # noqa: PLC2701

    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()

    checkpointer = get_checkpointer()
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

    cfg = await resolve_agent_context(db, conversation_id, user, checkpoint_id=checkpoint_id)
    cfg = with_regeneration_guidance(cfg, target_msg)
    await chat_service.touch_conversation(db, conversation_id)
    await chat_service.clear_active_branch_override(db, conversation_id)

    overwrite_input = await build_fork_overwrite_input(
        checkpointer, str(conversation_id), checkpoint_id
    )
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_regenerate",
        conversation_id=conversation_id,
        agent_id=uuid.UUID(cfg.agent_id) if cfg.agent_id else None,
        metadata={
            "message_id": str(data.message_id) if data.message_id else None,
        },
    )
    await db.commit()

    ctx = prepare_stream_context(conversation_id)
    stream_kwargs = ctx.as_stream_kwargs()
    stream_kwargs["artifact_recorder"] = build_artifact_recorder(
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        run_id=ctx.run_id,
    )
    return sse_handler(
        lambda: execute_agent_stream(
            cfg, overwrite_input, moldy_source="regenerate", **stream_kwargs
        ),
        log_msg=f"Agent regenerate failed for conversation {conversation_id}",
        user_msg="메시지 재생성 중 오류가 발생했습니다.",
        run_id=ctx.run_id,
        on_complete=ctx.finalize_callback(conversation_id),
        failure_probe=ctx.has_stream_error,
    )


@router.post(
    "/api/conversations/{conversation_id}/messages/switch-branch",
    status_code=204,
)
async def switch_branch(
    conversation_id: uuid.UUID,
    data: SwitchBranchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.services.thread_branch_service import checkpoint_exists

    if not await checkpoint_exists(
        get_checkpointer(),
        str(conversation_id),
        data.checkpoint_id,
    ):
        raise HTTPException(
            status_code=422,
            detail="checkpoint does not belong to this conversation.",
        )

    from sqlalchemy import update as _update

    from app.models.conversation import Conversation as _Conv

    await db.execute(
        _update(_Conv)
        .where(_Conv.id == conversation_id)
        .values(active_branch_checkpoint_id=data.checkpoint_id)
    )
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.switch_branch",
        conversation_id=conversation_id,
        agent_id=conv.agent_id,
        title=conv.title,
        metadata={"checkpoint_id": data.checkpoint_id},
    )
    await db.commit()
