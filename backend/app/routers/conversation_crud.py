from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import require_agent_run
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_org_context,
    owned_conversation,
    verify_csrf,
)
from app.error_codes import agent_not_found, conversation_not_found
from app.models.conversation import Conversation
from app.schemas.conversation import (
    ConversationAgentBrief,
    ConversationCreate,
    ConversationListEnvelope,
    ConversationResponse,
    ConversationSort,
    ConversationUpdate,
    ConversationWithAgentListEnvelope,
    ConversationWithAgentResponse,
)
from app.schemas.conversation_run import ConversationRunResponse
from app.schemas.conversation_scope import ConversationMutation
from app.services import chat_service, conversation_run_service
from app.services.agent_image_paths import build_agent_image_url
from app.services.conversation_audit_service import record_conversation_audit

router = APIRouter(tags=["conversations"])


def _conversation_with_agent_response(
    conversation: Conversation,
    active_run: ConversationRunResponse | None = None,
) -> ConversationWithAgentResponse:
    agent = conversation.agent
    base = ConversationResponse.model_validate(conversation).model_dump()
    base["active_run"] = active_run
    return ConversationWithAgentResponse(
        **base,
        agent=None
        if agent is None
        else ConversationAgentBrief(
            id=agent.id,
            name=agent.name,
            image_url=build_agent_image_url(
                agent.id,
                updated_at=agent.updated_at,
                image_path=agent.image_path,
            ),
        ),
    )


async def _active_runs_by_conversation(
    db: AsyncSession,
    items: list[Conversation],
    context: OrgContext,
) -> dict[uuid.UUID, ConversationRunResponse]:
    active_runs = await conversation_run_service.active_runs_for_conversations(
        db,
        [item.id for item in items],
        context=context,
    )
    return {
        conversation_id: ConversationRunResponse.model_validate(run)
        for conversation_id, run in active_runs.items()
    }


async def _conversation_responses_with_active_runs(
    db: AsyncSession,
    items: list[Conversation],
    context: OrgContext,
) -> list[ConversationResponse]:
    active_runs = await _active_runs_by_conversation(db, items, context)
    return [
        ConversationResponse.model_validate(item).model_copy(
            update={"active_run": active_runs.get(item.id)}
        )
        for item in items
    ]


@router.get(
    "/api/conversations/page",
    response_model=ConversationWithAgentListEnvelope,
)
@authz_policy(PRIVATE("conversations"))
async def list_global_conversations_page(
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None),
    q: str | None = Query(None, max_length=100),
    sort: ConversationSort = Query("updated"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    try:
        items, next_cursor, has_more = await chat_service.list_global_conversations_page(
            db,
            context,
            limit=limit,
            cursor=cursor,
            q=q,
            sort=sort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    active_runs = await _active_runs_by_conversation(db, items, context)
    return ConversationWithAgentListEnvelope(
        items=[
            _conversation_with_agent_response(item, active_run=active_runs.get(item.id))
            for item in items
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/api/conversations/{conversation_id}",
    response_model=ConversationWithAgentResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def get_conversation_detail(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
):
    conv = await chat_service.get_owned_ui_conversation_with_agent(db, conversation_id, context)
    if not conv:
        raise conversation_not_found()
    active_runs = await _active_runs_by_conversation(db, [conv], context)
    return _conversation_with_agent_response(conv, active_run=active_runs.get(conv.id))


@router.get(
    "/api/agents/{agent_id}/conversations/page",
    response_model=ConversationListEnvelope,
)
@authz_policy(PRIVATE("conversations"))
async def list_conversations_page(
    agent_id: uuid.UUID,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None),
    q: str | None = Query(None, max_length=100),
    sort: ConversationSort = Query("updated"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    if not await chat_service.is_agent_owned_by_user(db, agent_id, context):
        raise agent_not_found()
    try:
        items, next_cursor, has_more = await chat_service.list_conversations_page(
            db,
            agent_id,
            context=context,
            limit=limit,
            cursor=cursor,
            q=q,
            sort=sort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return ConversationListEnvelope(
        items=await _conversation_responses_with_active_runs(db, items, context),
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/api/agents/{agent_id}/conversations",
    response_model=list[ConversationResponse],
)
@authz_policy(PRIVATE("conversations"))
async def list_conversations(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, context)
    if not agent:
        raise agent_not_found()
    items = await chat_service.list_conversations(db, agent_id, context)
    return await _conversation_responses_with_active_runs(db, items, context)


@router.post(
    "/api/agents/{agent_id}/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
@authz_policy(PRIVATE("conversations"))
async def create_conversation(
    agent_id: uuid.UUID,
    data: ConversationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, context)
    if not agent:
        raise agent_not_found()
    await require_agent_run(context, agent_id, authz=authz)  # legacy gate above: in scope
    conv = await chat_service.create_conversation(db, agent_id, data.title, context=context)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.create",
        conversation_id=conv.id,
        agent_id=agent_id,
        title=conv.title,
    )
    await db.commit()
    return conv


@router.post(
    "/api/agents/{agent_id}/conversations/draft",
    response_model=ConversationResponse,
    status_code=201,
)
@authz_policy(PRIVATE("conversations"))
async def create_draft_conversation(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, context)
    if not agent:
        raise agent_not_found()
    await require_agent_run(context, agent_id, authz=authz)  # legacy gate above: in scope
    conv = await chat_service.create_conversation(db, agent_id, context=context, source="draft")
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.draft_create",
        conversation_id=conv.id,
        agent_id=agent_id,
        title=conv.title,
    )
    await db.commit()
    return conv


@router.patch(
    "/api/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def update_conversation(
    conversation_id: uuid.UUID,
    data: ConversationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    conv: Conversation = Depends(owned_conversation),
    context: OrgContext = Depends(get_org_context),
):
    updated = await chat_service.update_conversation(
        db, ConversationMutation(conv.id, context, data)
    )
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.update",
        conversation_id=updated.id,
        agent_id=updated.agent_id,
        title=updated.title,
        metadata={"changed_fields": sorted(data.model_fields_set)},
    )
    await db.commit()
    return updated


@router.post(
    "/api/conversations/{conversation_id}/read",
    response_model=ConversationResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def mark_conversation_read(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    conv: Conversation = Depends(owned_conversation),
    context: OrgContext = Depends(get_org_context),
):
    updated = await chat_service.mark_conversation_read(db, conv.id, context)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.mark_read",
        conversation_id=updated.id,
        agent_id=updated.agent_id,
        title=updated.title,
    )
    await db.commit()
    return updated


@router.delete("/api/conversations/{conversation_id}", status_code=204)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def delete_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    conv: Conversation = Depends(owned_conversation),
):
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.delete",
        conversation_id=conv.id,
        agent_id=conv.agent_id,
        title=conv.title,
    )
    await chat_service.delete_conversation(db, conv, context)
    await db.commit()
