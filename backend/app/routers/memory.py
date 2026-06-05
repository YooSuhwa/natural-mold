from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
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
async def get_user_memory_settings(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> UserMemorySettingsOut:
    settings = await memory_service.get_user_settings(db, user.id)
    return UserMemorySettingsOut.model_validate(settings)


@router.patch("/api/me/memory-settings", response_model=UserMemorySettingsOut)
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
async def get_agent_memory_settings(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> AgentMemorySettingsOut:
    settings = await memory_service.get_agent_settings(db, agent_id, user.id)
    if settings is None:
        raise agent_not_found()
    return AgentMemorySettingsOut.model_validate(settings)


@router.patch(
    "/api/agents/{agent_id}/memory-settings",
    response_model=AgentMemorySettingsOut,
)
async def update_agent_memory_settings(
    agent_id: uuid.UUID,
    payload: AgentMemorySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> AgentMemorySettingsOut:
    settings = await memory_service.update_agent_settings(db, agent_id, user.id, payload)
    if settings is None:
        raise agent_not_found()
    return AgentMemorySettingsOut.model_validate(settings)


@router.get("/api/memories", response_model=list[MemoryRecordOut])
async def list_memories(
    scope: Literal["all", "user", "agent"] = Query("all"),
    agent_id: uuid.UUID | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[MemoryRecordOut]:
    records = await memory_service.list_memory_records(
        db,
        user_id=user.id,
        scope=None if scope == "all" else scope,
        agent_id=agent_id,
        q=q,
    )
    return [MemoryRecordOut.model_validate(record) for record in records]


@router.post("/api/memories", response_model=MemoryRecordOut, status_code=201)
async def create_memory(
    payload: MemoryRecordCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MemoryRecordOut:
    record = await memory_service.create_memory_record(db, user_id=user.id, payload=payload)
    if record is None:
        raise agent_not_found()
    return MemoryRecordOut.model_validate(record)


@router.patch("/api/memories/{memory_id}", response_model=MemoryRecordOut)
async def update_memory(
    memory_id: uuid.UUID,
    payload: MemoryRecordUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MemoryRecordOut:
    record = await memory_service.update_memory_record(
        db,
        memory_id=memory_id,
        user_id=user.id,
        payload=payload,
    )
    if record is None:
        raise memory_not_found()
    return MemoryRecordOut.model_validate(record)


@router.delete("/api/memories/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    deleted = await memory_service.delete_memory_record(
        db,
        memory_id=memory_id,
        user_id=user.id,
    )
    if not deleted:
        raise memory_not_found()
    return Response(status_code=204)


@router.post("/api/memory-proposals", response_model=MemoryProposalOut, status_code=201)
async def create_memory_proposal(
    payload: MemoryProposalCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MemoryProposalOut:
    proposal = await memory_service.create_memory_proposal(
        db,
        user_id=user.id,
        payload=payload,
    )
    if proposal is None:
        raise agent_not_found()
    return MemoryProposalOut.model_validate(proposal)


@router.get("/api/memory-proposals/{proposal_id}", response_model=MemoryProposalOut)
async def get_memory_proposal(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> MemoryProposalOut:
    proposal = await memory_service.get_memory_proposal(
        db,
        proposal_id=proposal_id,
        user_id=user.id,
    )
    if proposal is None:
        raise memory_proposal_not_found()
    return MemoryProposalOut.model_validate(proposal)


@router.post(
    "/api/memory-proposals/{proposal_id}/approve",
    response_model=MemoryProposalApprovalOut,
)
async def approve_memory_proposal(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MemoryProposalApprovalOut:
    approved = await memory_service.approve_memory_proposal(
        db,
        proposal_id=proposal_id,
        user_id=user.id,
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
async def edit_and_approve_memory_proposal(
    proposal_id: uuid.UUID,
    payload: MemoryProposalEditApprove,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MemoryProposalApprovalOut:
    approved = await memory_service.approve_memory_proposal(
        db,
        proposal_id=proposal_id,
        user_id=user.id,
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
async def reject_memory_proposal(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MemoryProposalOut:
    proposal = await memory_service.reject_memory_proposal(
        db,
        proposal_id=proposal_id,
        user_id=user.id,
    )
    if proposal is None:
        raise memory_proposal_not_found()
    return MemoryProposalOut.model_validate(proposal)
