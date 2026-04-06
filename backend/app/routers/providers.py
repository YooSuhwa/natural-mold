from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.exceptions import NotFoundError
from app.schemas.llm_provider import (
    DiscoveredModel,
    ProviderCreate,
    ProviderResponse,
    ProviderTestResponse,
    ProviderUpdate,
)
from app.services import model_discovery, provider_service

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("", response_model=list[ProviderResponse])
async def list_providers(db: AsyncSession = Depends(get_db)):
    return await provider_service.list_providers(db)


@router.post("", response_model=ProviderResponse, status_code=201)
async def create_provider(data: ProviderCreate, db: AsyncSession = Depends(get_db)):
    provider = await provider_service.create_provider(db, data)
    return {
        **{c.key: getattr(provider, c.key) for c in provider.__table__.columns},
        "has_api_key": provider.api_key_encrypted is not None,
        "model_count": 0,
    }


@router.put("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: uuid.UUID, data: ProviderUpdate, db: AsyncSession = Depends(get_db)
):
    provider = await provider_service.update_provider(db, provider_id, data)
    if not provider:
        raise NotFoundError("PROVIDER_NOT_FOUND", "프로바이더를 찾을 수 없습니다")
    # Re-fetch with model count
    rows = await provider_service.list_providers(db)
    for row in rows:
        if row["id"] == provider_id:
            return row
    return {
        **{c.key: getattr(provider, c.key) for c in provider.__table__.columns},
        "has_api_key": provider.api_key_encrypted is not None,
        "model_count": 0,
    }


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    deleted = await provider_service.delete_provider(db, provider_id)
    if not deleted:
        raise NotFoundError("PROVIDER_NOT_FOUND", "프로바이더를 찾을 수 없습니다")


@router.post("/{provider_id}/test", response_model=ProviderTestResponse)
async def test_provider(provider_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    provider = await provider_service.get_provider(db, provider_id)
    if not provider:
        raise NotFoundError("PROVIDER_NOT_FOUND", "프로바이더를 찾을 수 없습니다")
    success, message, count = await model_discovery.test_connection(provider)
    return ProviderTestResponse(success=success, message=message, models_count=count)


@router.get("/{provider_id}/discover-models", response_model=list[DiscoveredModel])
async def discover_models(provider_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    provider = await provider_service.get_provider(db, provider_id)
    if not provider:
        raise NotFoundError("PROVIDER_NOT_FOUND", "프로바이더를 찾을 수 없습니다")
    return await model_discovery.discover_models(provider)
