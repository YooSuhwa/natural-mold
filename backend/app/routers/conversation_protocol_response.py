"""``run.start`` post-acceptance/dispatch/response blocks and the resume wait helper.

The ``run.start`` blocks are verbatim, ordered slices of the original
``conversation_agent_protocol_commands._handle_run_start_command`` body.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.routers.conversation_agent_protocol_contracts import command_success
from app.routers.conversation_protocol_persistence import QueuedRunStart
from app.routers.conversation_protocol_start import RunStartEntry
from app.schemas.conversation_scope import ConversationAutoTitle, ConversationPromotion
from app.services import chat_service, conversation_run_service
from app.services.chat_resource_context_payload import resource_context_user_message
from app.services.conversation_audit_service import record_conversation_audit

if TYPE_CHECKING:
    from app.agent_runtime.runtime_config import AgentConfig
    from app.models.conversation_run import ConversationRun

# M8-2 — 인터럽트 SSE 이벤트는 스트림 도중 즉시 클라이언트에 flush되지만,
# 부모 run의 "interrupted" 상태 커밋은 워커 finalize 단계다. 카드가 보이자마자
# 승인하면 전이 전에 resume이 도착할 수 있어, 활성 run이 전이를 마칠 때까지
# 짧게 기다린다 (활성 run이 아예 없으면 진짜 not-found — 즉시 포기).
_RESUME_INTERRUPT_WAIT_TIMEOUT_S = 2.0
_RESUME_INTERRUPT_WAIT_INTERVAL_S = 0.05


async def _wait_for_interrupted_parent_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> Any:
    deadline = time.monotonic() + _RESUME_INTERRUPT_WAIT_TIMEOUT_S
    while True:
        active = await conversation_run_service.get_active_run(
            db, conversation_id=conversation_id, context=context
        )
        if active is None or time.monotonic() >= deadline:
            # 전이 직후 창일 수 있으니 마지막으로 한 번 더 조회하고 포기한다.
            return await conversation_run_service.get_latest_interrupted_run(
                db, conversation_id=conversation_id, context=context
            )
        await asyncio.sleep(_RESUME_INTERRUPT_WAIT_INTERVAL_S)
        run = await conversation_run_service.get_latest_interrupted_run(
            db, conversation_id=conversation_id, context=context
        )
        if run is not None:
            return run


async def dispatch_queued_run_start(
    entry: RunStartEntry, strategy: str, queued_start: QueuedRunStart
) -> JSONResponse:
    conversation, command, db = entry.conversation, entry.command, entry.db
    queued, enqueue_result, active = queued_start
    if strategy == "interrupt" and enqueue_result.created and active is not None:
        from app.services.conversation_run_worker import get_run_task_registry

        get_run_task_registry().request_cancel(active.id, reason="steer")
    else:
        from app.services.conversation_run_queue_worker import (
            dispatch_next_for_conversation,
        )

        await dispatch_next_for_conversation(conversation.id)
        await db.refresh(queued)
    return command_success(
        command,
        conversation=conversation,
        thread_id=str(conversation.id),
        run_id=str(queued.run_id) if queued.run_id is not None else None,
        input_id=str(queued.id),
        input_status=queued.status,
        revision=queued.revision,
        position=queued.position,
    )


async def finalize_direct_run_start(
    entry: RunStartEntry,
    attachment_ids: list[uuid.UUID],
    preview: str | None,
    cfg: AgentConfig,
    run: ConversationRun,
) -> uuid.UUID:
    conversation, request, db, user = entry.conversation, entry.request, entry.db, entry.user
    context = entry.context
    run_id = run.id
    if conversation.source == "draft":
        await chat_service.promote_draft_conversation(
            db,
            ConversationPromotion(conversation.id, context, preview),
        )
    elif preview:
        await chat_service.maybe_set_auto_title(
            db, ConversationAutoTitle(conversation.id, context, preview)
        )
    await chat_service.touch_conversation(db, conversation.id, context)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_send",
        conversation_id=conversation.id,
        agent_id=require_conversation_agent_id(conversation),
        metadata={
            "content_length": len(preview or ""),
            "attachment_count": len(attachment_ids),
            "source": "langgraph_protocol",
        },
    )
    if attachment_ids:
        await chat_service.link_attachments_to_conversation(
            db,
            conversation_id=conversation.id,
            context=context,
            attachment_ids=attachment_ids,
        )
        if cfg.runtime_profile == "skill_builder" and cfg.draft_workspace_path:
            # 빌더 챗 (AD-2/§6-3): 이번 턴 첨부를 드래프트 워크스페이스
            # ``inputs/``로 **복사**한다 — uploads 마운트 금지.
            from app.services import skill_draft_workspace

            await skill_draft_workspace.copy_conversation_attachments_to_inputs(
                db,
                storage_path=cfg.draft_workspace_path,
                attachment_ids=attachment_ids,
                context=context,
            )
    await db.commit()
    return run_id


async def dispatch_direct_run_start(
    entry: RunStartEntry,
    run_id: uuid.UUID,
    cfg: AgentConfig,
    runtime_input_payload: dict[str, Any],
    run_source: str,
    attachment_ids: list[uuid.UUID],
) -> JSONResponse:
    conversation, command, user = entry.conversation, entry.command, entry.user
    start_run, executor_fn = entry.start_run, entry.executor_fn
    runtime_input_payload = resource_context_user_message(runtime_input_payload)

    await start_run(
        run_id=run_id,
        conversation_id=conversation.id,
        cfg=cfg,
        user=user,
        input_payload=runtime_input_payload,
        moldy_source=run_source,
        executor_fn=executor_fn,
        attachment_ids=attachment_ids,
    )
    return command_success(
        command,
        conversation=conversation,
        thread_id=str(conversation.id),
        run_id=str(run_id),
    )
