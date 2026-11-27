from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import bind_request_authz
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.dependencies import get_db, get_org_context, verify_csrf
from app.schemas.conversation_child_task import (
    ChildInstructionCreate,
    ChildTaskPage,
    ChildTaskResponse,
)
from app.services import child_tasks_api

router = APIRouter(
    prefix="/api/conversations/{conversation_id}/child-tasks",
    tags=["conversation-child-tasks"],
    dependencies=[Depends(bind_request_authz)],
)
Db = Annotated[AsyncSession, Depends(get_db)]
Context = Annotated[OrgContext, Depends(get_org_context)]


@router.get("")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def list_conversation_child_tasks(
    conversation_id: UUID,
    db: Db,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=2147483647)] = 0,
) -> ChildTaskPage:
    return await child_tasks_api.list_child_tasks(
        db, conversation_id, context, limit=limit, offset=offset
    )


@router.post("/{child_id}/instructions", dependencies=[Depends(verify_csrf)])
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def add_conversation_child_instruction(
    conversation_id: UUID,
    child_id: UUID,
    payload: ChildInstructionCreate,
    db: Db,
    context: Context,
) -> ChildTaskResponse:
    return await child_tasks_api.instruct_child(db, conversation_id, child_id, context, payload)


@router.post("/{child_id}/cancel", dependencies=[Depends(verify_csrf)])
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def cancel_conversation_child_task(
    conversation_id: UUID,
    child_id: UUID,
    db: Db,
    context: Context,
) -> ChildTaskResponse:
    return await child_tasks_api.cancel_child(db, conversation_id, child_id, context)


@router.get("/{child_id}/wait")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def wait_for_conversation_child_task(
    conversation_id: UUID,
    child_id: UUID,
    db: Db,
    context: Context,
    timeout_seconds: Annotated[float, Query(ge=0, le=20, allow_inf_nan=False)] = 10,
) -> ChildTaskResponse:
    return await child_tasks_api.wait_for_child(
        db, conversation_id, child_id, context, timeout_seconds
    )
