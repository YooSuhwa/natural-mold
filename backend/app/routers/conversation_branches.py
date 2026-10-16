from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_org_context,
    owned_conversation,
    verify_csrf,
)
from app.error_codes import conversation_not_found
from app.models.conversation import Conversation
from app.routers import conversation_branch_entries
from app.routers.conversation_messages import _broker_resume_generator
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
from app.services.conversation_stream_service import (
    execute_agent_stream,
    resolve_agent_context,
    sse_response,
)

router = APIRouter(tags=["conversations"])


def _cfg_agent_uuid(cfg) -> uuid.UUID:
    if not cfg.agent_id:
        raise conversation_not_found()
    return uuid.UUID(cfg.agent_id)


__all__ = ["ConversationBranchMutation", "HTTPException", "OwnedExecutionRequest"]
__all__ += ["_broker_resume_generator", "accept_owned_execution", "active_checkpoint_from_override"]
__all__ += ["chat_service", "execute_agent_stream", "find_message_in_checkpoints"]
__all__ += ["record_conversation_audit", "require_conversation_agent_id", "resolve_agent_context"]
__all__ += ["resolve_branch_checkpoint", "sse_response", "start_conversation_run"]
__all__ += ["with_regeneration_guidance"]


@router.post("/api/conversations/{conversation_id}/messages/edit")
async def edit_message(
    conversation_id: uuid.UUID,
    data: EditMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    return await conversation_branch_entries.edit_message(
        conversation_id=conversation_id,
        data=data,
        request=request,
        db=db,
        user=user,
        context=context,
    )


@router.post("/api/conversations/{conversation_id}/messages/regenerate")
async def regenerate_message(
    conversation_id: uuid.UUID,
    data: RegenerateMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    conv: Conversation = Depends(owned_conversation),
):
    return await conversation_branch_entries.regenerate_message(
        conversation_id=conversation_id,
        data=data,
        request=request,
        db=db,
        user=user,
        context=context,
        conv=conv,
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
    conv: Conversation = Depends(owned_conversation),
    context: OrgContext = Depends(get_org_context),
):
    await conversation_branch_entries.switch_branch(
        conversation_id=conversation_id,
        data=data,
        request=request,
        db=db,
        user=user,
        conv=conv,
        context=context,
    )
