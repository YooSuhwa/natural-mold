from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import require_scoped_agent_permission
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import agent_not_found, memory_not_found, memory_proposal_not_found
from app.schemas.memory import (
    AgentMemorySettingsOut,
    AgentMemorySettingsUpdate,
    MemoryProposalApprovalOut,
    MemoryProposalCreate,
    MemoryProposalEditApprove,
    MemoryProposalOut,
    MemoryRecordCreate,
    MemoryRecordOut,
    MemoryRecordUpdate,
    UserMemorySettingsOut,
    UserMemorySettingsUpdate,
)
from app.services import memory_service

router = APIRouter(tags=["memory"])


@router.get("/api/me/memory-settings", response_model=UserMemorySettingsOut)
@authz_policy(PRIVATE("user_memory_settings"))
async def get_user_memory_settings(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> UserMemorySettingsOut:
    settings = await memory_service.get_user_settings(db, user.id)
    return UserMemorySettingsOut.model_validate(settings)


@router.patch("/api/me/memory-settings", response_model=UserMemorySettingsOut)
@authz_policy(PRIVATE("user_memory_settings"))
async def update_user_memory_settings(
    payload: UserMemorySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> UserMemorySettingsOut:
    settings = await memory_service.update_user_settings(db, user.id, payload)
    return UserMemorySettingsOut.model_validate(settings)


@router.get(
    "/api/agents/{agent_id}/memory-settings",
    response_model=AgentMemorySettingsOut,
)
# TODO(F-09): RES agent#can_edit is checked, but memory_service is still owner-scoped.
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def get_agent_memory_settings(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> AgentMemorySettingsOut:
    await require_scoped_agent_permission(db, context, agent_id, "can_edit", authz=authz)
    settings = await memory_service.get_agent_settings(db, agent_id, context)
    if settings is None:
        raise agent_not_found()
    return AgentMemorySettingsOut.model_validate(settings)


@router.patch(
    "/api/agents/{agent_id}/memory-settings",
    response_model=AgentMemorySettingsOut,
)
# TODO(F-09): RES agent#can_edit is checked, but memory_service is still owner-scoped.
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def update_agent_memory_settings(
    agent_id: uuid.UUID,
    payload: AgentMemorySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> AgentMemorySettingsOut:
    await require_scoped_agent_permission(db, context, agent_id, "can_edit", authz=authz)
    settings = await memory_service.update_agent_settings(db, agent_id, context, payload)
    if settings is None:
        raise agent_not_found()
    return AgentMemorySettingsOut.model_validate(settings)


@router.get("/api/memories", response_model=list[MemoryRecordOut])
@authz_policy(PRIVATE("memory_records"))
async def list_memories(
    scope: Literal["all", "user", "agent"] = Query("all"),
    agent_id: uuid.UUID | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> list[MemoryRecordOut]:
    records = await memory_service.list_memory_records(
        db,
        context=context,
        scope=None if scope == "all" else scope,
        agent_id=agent_id,
        q=q,
    )
    return [MemoryRecordOut.model_validate(record) for record in records]


@router.post("/api/memories", response_model=MemoryRecordOut, status_code=201)
@authz_policy(PRIVATE("memory_records"))
async def create_memory(
    payload: MemoryRecordCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryRecordOut:
    record = await memory_service.create_memory_record(db, context=context, payload=payload)
    if record is None:
        raise agent_not_found()
    return MemoryRecordOut.model_validate(record)


@router.patch("/api/memories/{memory_id}", response_model=MemoryRecordOut)
@authz_policy(PRIVATE("memory_records", param="memory_id"))
async def update_memory(
    memory_id: uuid.UUID,
    payload: MemoryRecordUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryRecordOut:
    record = await memory_service.update_memory_record(
        db,
        memory_id=memory_id,
        context=context,
        payload=payload,
    )
    if record is None:
        raise memory_not_found()
    return MemoryRecordOut.model_validate(record)


@router.delete("/api/memories/{memory_id}", status_code=204)
@authz_policy(PRIVATE("memory_records", param="memory_id"))
async def delete_memory(
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    deleted = await memory_service.delete_memory_record(
        db,
        memory_id=memory_id,
        context=context,
    )
    if not deleted:
        raise memory_not_found()
    return Response(status_code=204)


@router.post("/api/memory-proposals", response_model=MemoryProposalOut, status_code=201)
@authz_policy(PRIVATE("memory_proposals"))
async def create_memory_proposal(
    payload: MemoryProposalCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryProposalOut:
    proposal = await memory_service.create_memory_proposal(
        db,
        context=context,
        payload=payload,
    )
    if proposal is None:
        raise agent_not_found()
    return MemoryProposalOut.model_validate(proposal)


@router.get("/api/memory-proposals/{proposal_id}", response_model=MemoryProposalOut)
@authz_policy(PRIVATE("memory_proposals", param="proposal_id"))
async def get_memory_proposal(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> MemoryProposalOut:
    proposal = await memory_service.get_memory_proposal(
        db,
        proposal_id=proposal_id,
        context=context,
    )
    if proposal is None:
        raise memory_proposal_not_found()
    return MemoryProposalOut.model_validate(proposal)


@router.post(
    "/api/memory-proposals/{proposal_id}/approve",
    response_model=MemoryProposalApprovalOut,
)
@authz_policy(PRIVATE("memory_proposals", param="proposal_id"))
async def approve_memory_proposal(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryProposalApprovalOut:
    approved = await memory_service.approve_memory_proposal(
        db,
        proposal_id=proposal_id,
        context=context,
    )
    if approved is None:
        raise memory_proposal_not_found()
    proposal, memory = approved
    return MemoryProposalApprovalOut(
        proposal=MemoryProposalOut.model_validate(proposal),
        memory=MemoryRecordOut.model_validate(memory),
    )


@router.post(
    "/api/memory-proposals/{proposal_id}/edit-and-approve",
    response_model=MemoryProposalApprovalOut,
)
@authz_policy(PRIVATE("memory_proposals", param="proposal_id"))
async def edit_and_approve_memory_proposal(
    proposal_id: uuid.UUID,
    payload: MemoryProposalEditApprove,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryProposalApprovalOut:
    approved = await memory_service.approve_memory_proposal(
        db,
        proposal_id=proposal_id,
        context=context,
        content=payload.content,
        reason=payload.reason,
    )
    if approved is None:
        raise memory_proposal_not_found()
    proposal, memory = approved
    return MemoryProposalApprovalOut(
        proposal=MemoryProposalOut.model_validate(proposal),
        memory=MemoryRecordOut.model_validate(memory),
    )


@router.post("/api/memory-proposals/{proposal_id}/reject", response_model=MemoryProposalOut)
@authz_policy(PRIVATE("memory_proposals", param="proposal_id"))
async def reject_memory_proposal(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryProposalOut:
    proposal = await memory_service.reject_memory_proposal(
        db,
        proposal_id=proposal_id,
        context=context,
    )
    if proposal is None:
        raise memory_proposal_not_found()
    return MemoryProposalOut.model_validate(proposal)
