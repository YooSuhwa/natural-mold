"""Model catalog endpoints.

The greenfield Credential domain owns LLM API keys, so model rows are now
plain reference data. This router only exposes the read-only catalog used by
the frontend's "default model" picker; CRUD will be reintroduced (or moved)
in M6 once the new admin UI lands.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.error_codes import model_not_found
from app.models.user import User
from app.services import model_service

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def list_models(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return await model_service.list_models(db)


@router.get("/{model_id}")
async def get_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    model = await model_service.get_model(db, model_id)
    if not model:
        raise model_not_found()
    return {
        "id": model.id,
        "provider": model.provider,
        "model_name": model.model_name,
        "display_name": model.display_name,
        "base_url": model.base_url,
        "is_default": model.is_default,
        "context_window": model.context_window,
        "input_modalities": model.input_modalities,
        "output_modalities": model.output_modalities,
    }
