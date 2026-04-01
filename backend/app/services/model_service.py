from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.schemas.model import ModelCreate


async def list_models(db: AsyncSession) -> list[Model]:
    result = await db.execute(select(Model).order_by(Model.is_default.desc(), Model.display_name))
    return list(result.scalars().all())


async def get_model(db: AsyncSession, model_id: uuid.UUID) -> Model | None:
    result = await db.execute(select(Model).where(Model.id == model_id))
    return result.scalar_one_or_none()


async def create_model(db: AsyncSession, data: ModelCreate) -> Model:
    model = Model(
        provider=data.provider,
        model_name=data.model_name,
        display_name=data.display_name,
        base_url=data.base_url,
        api_key_encrypted=data.api_key,
        is_default=data.is_default,
        cost_per_input_token=data.cost_per_input_token,
        cost_per_output_token=data.cost_per_output_token,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model


async def delete_model(db: AsyncSession, model_id: uuid.UUID) -> bool:
    model = await get_model(db, model_id)
    if not model:
        return False
    await db.delete(model)
    await db.commit()
    return True
