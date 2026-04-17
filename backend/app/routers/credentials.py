from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.schemas.credential import (
    CredentialCreate,
    CredentialProviderDef,
    CredentialResponse,
    CredentialUpdate,
    CredentialUsageResponse,
)
from app.services import credential_service
from app.services.credential_registry import CREDENTIAL_PROVIDERS
from app.services.encryption import decrypt_api_key

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


def _to_response(cred) -> CredentialResponse:
    """Convert Credential ORM to CredentialResponse (never expose decrypted data)."""
    try:
        field_keys = list(json.loads(decrypt_api_key(cred.data_encrypted)).keys())
    except Exception:
        field_keys = []
    return CredentialResponse(
        id=cred.id,
        name=cred.name,
        credential_type=cred.credential_type,
        provider_name=cred.provider_name,
        is_active=cred.is_active,
        has_data=bool(cred.data_encrypted),
        field_keys=field_keys,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


@router.get("/providers", response_model=list[CredentialProviderDef])
async def list_providers():
    return [
        CredentialProviderDef(key=key, **value)
        for key, value in CREDENTIAL_PROVIDERS.items()
    ]


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    creds = await credential_service.list_credentials(db, user.id)
    return [_to_response(c) for c in creds]


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    data: CredentialCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    cred = await credential_service.create_credential(db, user.id, data)
    return _to_response(cred)


@router.put("/{credential_id}", response_model=CredentialResponse)
async def update_credential(
    credential_id: uuid.UUID,
    data: CredentialUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    cred = await credential_service.update_credential(
        db, credential_id, user.id, data
    )
    return _to_response(cred)


@router.delete("/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    await credential_service.delete_credential(db, credential_id, user.id)


@router.get("/{credential_id}/usage", response_model=CredentialUsageResponse)
async def get_credential_usage(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    counts = await credential_service.get_usage_count(
        db, credential_id, user.id
    )
    return CredentialUsageResponse(
        credential_id=credential_id,
        tool_count=counts["tool_count"],
        mcp_server_count=counts["mcp_server_count"],
    )
