from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.schemas.model import ModelCreate, ModelResponse
from app.services import model_service

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=list[ModelResponse])
async def list_models(db: AsyncSession = Depends(get_db)):
    return await model_service.list_models(db)


@router.post("", response_model=ModelResponse, status_code=201)
async def create_model(data: ModelCreate, db: AsyncSession = Depends(get_db)):
    return await model_service.create_model(db, data)


@router.delete("/{model_id}", status_code=204)
async def delete_model(model_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    deleted = await model_service.delete_model(db, model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Model not found")
