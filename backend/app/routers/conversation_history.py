from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context
from app.schemas.conversation_history import ConversationHistoryResponse
from app.services.conversation_history_service import (
    ConversationHistoryReadRequest,
    read_conversation_history,
)

router = APIRouter(tags=["conversation-history"])


@dataclass(frozen=True, slots=True)
class ConversationHistoryAccess:
    user: CurrentUser
    context: OrgContext


async def conversation_history_access(
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> ConversationHistoryAccess:
    return ConversationHistoryAccess(user, context)


@router.get(
    "/api/conversations/{conversation_id}/history",
    response_model=ConversationHistoryResponse,
)
@authz_policy(SELF)
async def get_conversation_history(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    access: ConversationHistoryAccess = Depends(conversation_history_access),
) -> ConversationHistoryResponse:
    return await read_conversation_history(
        ConversationHistoryReadRequest(db, conversation_id, access.user, access.context)
    )
