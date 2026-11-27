from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import bind_request_authz
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.config import settings
from app.dependencies import get_db, get_org_context, owned_conversation, verify_csrf
from app.schemas.conversation_run_steer import (
    ConversationRunSteerCreate,
    ConversationRunSteerResponse,
)
from app.services import conversation_run_steers

router = APIRouter(prefix="/api/conversations/{conversation_id}", tags=["conversation-run-steers"])


@router.post(
    "/runs/{run_id}/steers",
    response_model=ConversationRunSteerResponse,
    dependencies=[Depends(verify_csrf), Depends(owned_conversation), Depends(bind_request_authz)],
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def submit_run_steer(
    conversation_id: UUID,
    run_id: UUID,
    payload: ConversationRunSteerCreate,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> ConversationRunSteerResponse:
    if not settings.chat_steer_enabled:
        raise HTTPException(status_code=404, detail="Run steering is disabled")
    item = await conversation_run_steers.admit_steer(
        db,
        conversation_id=conversation_id,
        run_id=run_id,
        context=context,
        client_request_id=payload.client_request_id,
        text=payload.text,
    )
    receipt = ConversationRunSteerResponse.model_validate(item)
    await db.commit()
    return receipt


@router.get(
    "/runs/{run_id}/steers",
    response_model=list[ConversationRunSteerResponse],
    dependencies=[Depends(owned_conversation)],
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def list_run_steers(
    conversation_id: UUID,
    run_id: UUID,
    before_sequence: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> list[ConversationRunSteerResponse]:
    rows = await conversation_run_steers.list_steers(
        db,
        conversation_id=conversation_id,
        context=context,
        run_id=run_id,
        before_sequence=before_sequence,
        limit=limit,
    )
    return [ConversationRunSteerResponse.model_validate(row) for row in rows]


@router.get(
    "/steers",
    response_model=list[ConversationRunSteerResponse],
    dependencies=[Depends(owned_conversation)],
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def list_conversation_steers(
    conversation_id: UUID,
    before_sequence: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> list[ConversationRunSteerResponse]:
    rows = await conversation_run_steers.list_steers(
        db,
        conversation_id=conversation_id,
        context=context,
        before_sequence=before_sequence,
        limit=limit,
    )
    return [ConversationRunSteerResponse.model_validate(row) for row in rows]
