from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import dependencies
from app.authz.memory_agent_access import agent_edit_context
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import agent_not_found, memory_not_found, memory_proposal_not_found
from app.exceptions import ValidationError
from app.schemas.memory import (
    AgentMemorySettingsOut,
    AgentMemorySettingsUpdate,
    ApprovalAxes,
    MemoryActivityOut,
    MemoryBulkDelete,
    MemoryDeletionReceipt,
    MemoryPolicyOut,
    MemoryProposalApprovalOut,
    MemoryProposalCreate,
    MemoryProposalEditApprove,
    MemoryProposalOut,
    MemoryRecordCreate,
    MemoryRecordOut,
    MemoryRecordUpdate,
    MemoryRevisionOut,
    MemoryRevoke,
    UserMemorySettingsOut,
    UserMemorySettingsUpdate,
)
from app.services import memory_lifecycle, memory_service, memory_writes
from app.services.memory_policy import effective_mode, mode_axes, tenant_kind, tenant_memory_policy

router = APIRouter(tags=["memory"])


@router.get("/api/me/memory-settings", response_model=UserMemorySettingsOut)
@authz_policy(PRIVATE("user_memory_settings"))
async def get_user_memory_settings(
    db: AsyncSession = Depends(dependencies.get_company_db),
    user: CurrentUser = Depends(get_current_user),
) -> UserMemorySettingsOut:
    settings = await memory_service.get_user_settings(db, user.id)
    return UserMemorySettingsOut.model_validate(settings)


@router.patch("/api/me/memory-settings", response_model=UserMemorySettingsOut)
@authz_policy(PRIVATE("user_memory_settings"))
async def update_user_memory_settings(
    payload: UserMemorySettingsUpdate,
    _csrf: None = Depends(verify_csrf),
    db: AsyncSession = Depends(dependencies.get_company_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> UserMemorySettingsOut:
    if payload.approval_mode is not None:
        tenant = tenant_memory_policy(
            await memory_writes.tenant_settings_for(db, context.tenant_id)
        )
        if payload.approval_mode not in tenant.allowed_modes:
            raise ValidationError("MEMORY_MODE_NOT_ALLOWED", "이 승인 모드는 쓸 수 없습니다")
    settings = await memory_service.update_user_settings(db, user.id, payload)
    return UserMemorySettingsOut.model_validate(settings)


@router.get("/api/me/memory-policy", response_model=MemoryPolicyOut)
@authz_policy(PRIVATE("user_memory_settings"))
async def get_memory_policy(
    db: AsyncSession = Depends(dependencies.get_company_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> MemoryPolicyOut:
    """Plan 15 (5.11, Q9): the mode in force and the tenant's allowed modes."""
    raw = await memory_writes.tenant_settings_for(db, context.tenant_id)
    tenant = tenant_memory_policy(raw)
    settings = await memory_service.get_user_settings(db, user.id)
    mode = effective_mode(
        memory_enabled=settings.memory_enabled,
        approval_mode=settings.approval_mode,
        legacy_write_policy=settings.memory_write_policy,
        tenant=tenant,
    )
    return MemoryPolicyOut(
        tenant_kind=tenant_kind(raw),
        effective_mode=mode,  # type: ignore[arg-type]
        axes=ApprovalAxes(**mode_axes(mode, settings.approval_axes)),
        allowed_modes=sorted(tenant.allowed_modes),  # type: ignore[arg-type]
        default_mode=tenant.default_mode,  # type: ignore[arg-type]
        consolidation_enabled=tenant.consolidation_enabled and settings.consolidation_enabled,
    )


@router.get("/api/agents/{agent_id}/memory-settings", response_model=AgentMemorySettingsOut)
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def get_agent_memory_settings(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> AgentMemorySettingsOut:
    scope = await agent_edit_context(db, authz, context, agent_id)  # F-09: can_edit holders
    settings = await memory_service.get_agent_settings(db, agent_id, scope)
    if settings is None:
        raise agent_not_found()
    return AgentMemorySettingsOut.model_validate(settings)


@router.patch(
    "/api/agents/{agent_id}/memory-settings",
    response_model=AgentMemorySettingsOut,
)
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
    owner = await agent_edit_context(db, authz, context, agent_id)  # F-09: editors too
    update = memory_service.update_agent_settings
    settings = await update(db, agent_id, owner, payload, actor=context)
    if settings is None:
        raise agent_not_found()
    return AgentMemorySettingsOut.model_validate(settings)


@router.get("/api/memories", response_model=list[MemoryRecordOut])
@authz_policy(PRIVATE("memory_records"))
async def list_memories(
    scope: Literal["all", "user", "agent"] = Query("all"),
    agent_id: uuid.UUID | None = None,
    q: str | None = None,
    status: Literal["active", "revoked", "review", "all"] = Query("active"),
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
        status=status,
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
    # A person typed it on the memory screens: ``user_stated`` (plan 15, 5.3-1), still checked.
    result = await memory_writes.write_memory(
        db,
        context=context,
        write=memory_writes.MemoryWrite(
            scope=payload.scope,
            content=payload.content,
            reason=payload.reason,
            agent_id=payload.agent_id,
            conversation_id=payload.source_conversation_id,
            source_run_id=payload.source_run_id,
        ),
    )
    if result.record is None:
        raise agent_not_found()
    return MemoryRecordOut.model_validate(result.record)


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


@router.get("/api/memories/export")
@authz_policy(PRIVATE("memory_records"))
async def export_memories(
    format: Literal["json", "markdown"] = Query("json"),
    scope: Literal["all", "user", "agent"] = Query("all"),
    agent_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> Response:
    """Plan 15 (5.7): everything the person owns, as JSON or readable Markdown."""
    body = await memory_lifecycle.export(db, context=context, scope=scope, agent_id=agent_id)
    if format == "markdown":
        return PlainTextResponse(
            memory_lifecycle.export_markdown(body),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="memories.md"'},
        )
    return Response(
        content=json.dumps(body, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="memories.json"'},
    )


@router.get("/api/me/memory-activity", response_model=list[MemoryActivityOut])
@authz_policy(PRIVATE("memory_records"))
async def list_memory_activity(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> list[MemoryActivityOut]:
    rows = await memory_lifecycle.activity(db, context=context)
    return [
        MemoryActivityOut(
            id=row.id,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            created_at=row.created_at,
            metadata=dict(row.event_metadata or {}),
        )
        for row in rows
    ]


@router.get("/api/memories/delete-scope/count")
@authz_policy(PRIVATE("memory_records"))
async def count_memories_in_scope(
    scope: Literal["all", "user", "agent"] = Query("all"),
    agent_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> dict[str, int]:
    count = await memory_lifecycle.count_scope(db, context=context, scope=scope, agent_id=agent_id)
    return {"count": count}


@router.post("/api/memories/delete-scope", response_model=MemoryDeletionReceipt)
@authz_policy(PRIVATE("memory_records"))
async def delete_memories_in_scope(
    payload: MemoryBulkDelete,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryDeletionReceipt:
    return await memory_lifecycle.delete_scope(
        db,
        context=context,
        scope=payload.scope,
        agent_id=payload.agent_id,
        confirm_count=payload.confirm_count,
    )


@router.post("/api/memories/{memory_id}/revoke", response_model=MemoryRecordOut)
@authz_policy(PRIVATE("memory_records", param="memory_id"))
async def revoke_memory(
    memory_id: uuid.UUID,
    payload: MemoryRevoke,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryRecordOut:
    record = await memory_lifecycle.revoke(
        db, context=context, memory_id=memory_id, reason=payload.reason
    )
    if record is None:
        raise memory_not_found()
    return MemoryRecordOut.model_validate(record)


@router.post("/api/memories/{memory_id}/restore", response_model=MemoryRecordOut)
@authz_policy(PRIVATE("memory_records", param="memory_id"))
async def restore_memory(
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> MemoryRecordOut:
    record = await memory_lifecycle.restore(db, context=context, memory_id=memory_id)
    if record is None:
        raise memory_not_found()
    return MemoryRecordOut.model_validate(record)


@router.get("/api/memories/{memory_id}/revisions", response_model=list[MemoryRevisionOut])
@authz_policy(PRIVATE("memory_records", param="memory_id"))
async def list_memory_revisions(
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> list[MemoryRevisionOut]:
    rows = await memory_lifecycle.list_revisions(db, context=context, memory_id=memory_id)
    if rows is None:
        raise memory_not_found()
    return [MemoryRevisionOut.model_validate(row) for row in rows]


@router.get("/api/memory-proposals", response_model=list[MemoryProposalOut])
@authz_policy(PRIVATE("memory_proposals"))
async def list_memory_proposals(
    status: Literal["pending", "approved", "rejected", "all"] = Query("pending"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> list[MemoryProposalOut]:
    proposals = await memory_service.list_memory_proposals(db, context=context, status=status)
    return [MemoryProposalOut.model_validate(proposal) for proposal in proposals]


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
