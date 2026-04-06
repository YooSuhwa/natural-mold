from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.exceptions import NotFoundError
from app.models.user import User
from app.schemas.model import ModelBulkCreate, ModelCreate, ModelResponse, ModelUpdate
from app.services import model_service

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=list[ModelResponse])
async def list_models(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await model_service.list_models(db)


@router.post("", response_model=ModelResponse, status_code=201)
async def create_model(
    data: ModelCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return await model_service.create_model(db, data)


@router.post("/bulk", response_model=list[ModelResponse], status_code=201)
async def bulk_create_models(
    data: ModelBulkCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await model_service.bulk_create_models(db, data)
    if result is None:
        raise NotFoundError("PROVIDER_NOT_FOUND", "프로바이더를 찾을 수 없습니다")
    return result


@router.put("/{model_id}", response_model=ModelResponse)
async def update_model(
    model_id: uuid.UUID,
    data: ModelUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    model = await model_service.update_model(db, model_id, data)
    if not model:
        raise NotFoundError("MODEL_NOT_FOUND", "모델을 찾을 수 없습니다")
    return model


@router.delete("/{model_id}", status_code=204)
async def delete_model(
    model_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    deleted = await model_service.delete_model(db, model_id)
    if not deleted:
        raise NotFoundError("MODEL_NOT_FOUND", "모델을 찾을 수 없습니다")
