from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import service
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.schemas.agent_api import (
    AgentApiKeyCreate,
    AgentApiKeyCreatedResponse,
    AgentApiKeyListResponse,
    AgentDeploymentCandidateResponse,
    AgentDeploymentCreate,
    AgentDeploymentResponse,
    AgentDeploymentUpdate,
)
from app.services import audit_service

router = APIRouter(prefix="/api/agent-api", tags=["agent-api"])


async def _record_deployment_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    row: AgentDeployment,
    metadata: dict[str, object] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="agent_deployment",
        target_id=row.id,
        target_name_snapshot=row.public_id,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "agent_id": str(row.agent_id),
            "public_id": row.public_id,
            "status": row.status,
            "allow_streaming": row.allow_streaming,
            "allow_background": row.allow_background,
            "rate_limit_per_minute": row.rate_limit_per_minute,
            "daily_token_limit": row.daily_token_limit,
            **(metadata or {}),
        },
    )


async def _record_api_key_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    row: AgentApiKey,
    metadata: dict[str, object] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="agent_api_key",
        target_id=row.id,
        target_name_snapshot=row.name,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "key_id": row.key_id,
            "prefix": row.prefix,
            "last_four": row.last_four,
            "scopes": list(row.scopes or []),
            "allow_all_deployments": row.allow_all_deployments,
            "deployment_count": len(row.deployment_links or []),
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "revoked": row.revoked_at is not None,
            **(metadata or {}),
        },
    )


def _key_list_response(row) -> AgentApiKeyListResponse:
    return AgentApiKeyListResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        key_id=row.key_id,
        prefix=row.prefix,
        last_four=row.last_four,
        scopes=row.scopes,
        allow_all_deployments=row.allow_all_deployments,
        deployments=service.serialize_key_deployments(row),
        revoked_at=row.revoked_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        usage_count=row.usage_count,
        created_at=row.created_at,
    )


@router.get(
    "/deployment-candidates", response_model=list[AgentDeploymentCandidateResponse]
)
async def list_deployment_candidates(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await service.list_deployment_candidates(db, user.id)


@router.get("/deployments", response_model=list[AgentDeploymentResponse])
async def list_deployments(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows = await service.list_deployments(db, user.id)
    return [service.deployment_to_response(row) for row in rows]


@router.post("/deployments", response_model=AgentDeploymentResponse, status_code=201)
async def create_deployment(
    data: AgentDeploymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    row = await service.create_deployment(db, user.id, data)
    await _record_deployment_audit(
        db,
        user=user,
        request=request,
        action="agent_api.deployment_create",
        row=row,
    )
    await db.commit()
    return service.deployment_to_response(row)


@router.patch("/deployments/{deployment_id}", response_model=AgentDeploymentResponse)
async def update_deployment(
    deployment_id: uuid.UUID,
    data: AgentDeploymentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    row = await service.update_deployment(db, user.id, deployment_id, data)
    await _record_deployment_audit(
        db,
        user=user,
        request=request,
        action="agent_api.deployment_update",
        row=row,
        metadata={"changed_fields": sorted(data.model_fields_set)},
    )
    await db.commit()
    return service.deployment_to_response(row)


@router.get("/keys", response_model=list[AgentApiKeyListResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows = await service.list_api_keys(db, user.id)
    return [_key_list_response(row) for row in rows]


@router.post("/keys", response_model=AgentApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    data: AgentApiKeyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    row, cleartext = await service.create_api_key(db, user.id, data)
    await _record_api_key_audit(
        db,
        user=user,
        request=request,
        action="agent_api.key_create",
        row=row,
    )
    await db.commit()
    base = _key_list_response(row)
    return AgentApiKeyCreatedResponse(
        id=base.id,
        key=cleartext,
        key_id=base.key_id,
        prefix=base.prefix,
        last_four=base.last_four,
        scopes=base.scopes,
        allow_all_deployments=base.allow_all_deployments,
        deployments=base.deployments,
        expires_at=base.expires_at,
        created_at=base.created_at,
    )


@router.post("/keys/{api_key_id}/revoke", response_model=AgentApiKeyListResponse)
async def revoke_api_key(
    api_key_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    row = await service.revoke_api_key(db, user.id, api_key_id)
    await _record_api_key_audit(
        db,
        user=user,
        request=request,
        action="agent_api.key_revoke",
        row=row,
    )
    await db.commit()
    return _key_list_response(row)
