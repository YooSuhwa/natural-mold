from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.llm_provider import LLMProvider
from app.models.model import Model
from app.schemas.model import ModelBulkCreate, ModelCreate, ModelUpdate


async def list_models(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Model)
        .options(selectinload(Model.llm_provider))
        .order_by(Model.is_default.desc(), Model.display_name)
    )
    models = result.scalars().all()
    return [_model_to_response(m) for m in models]


async def get_model(db: AsyncSession, model_id: uuid.UUID) -> Model | None:
    result = await db.execute(
        select(Model).where(Model.id == model_id).options(selectinload(Model.llm_provider))
    )
    return result.scalar_one_or_none()


async def create_model(db: AsyncSession, data: ModelCreate) -> dict:
    model = Model(
        provider=data.provider,
        model_name=data.model_name,
        display_name=data.display_name,
        provider_id=data.provider_id,
        base_url=data.base_url,
        api_key_encrypted=data.api_key,
        is_default=data.is_default,
        cost_per_input_token=data.cost_per_input_token,
        cost_per_output_token=data.cost_per_output_token,
        context_window=data.context_window,
        input_modalities=data.input_modalities,
        output_modalities=data.output_modalities,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    # Load provider relationship
    if model.provider_id:
        result = await db.execute(
            select(Model).where(Model.id == model.id).options(selectinload(Model.llm_provider))
        )
        model = result.scalar_one()
    return _model_to_response(model)


async def update_model(db: AsyncSession, model_id: uuid.UUID, data: ModelUpdate) -> dict | None:
    model = await get_model(db, model_id)
    if not model:
        return None
    update_data = data.model_dump(exclude_unset=True)
    if "api_key" in update_data:
        update_data["api_key_encrypted"] = update_data.pop("api_key")
    for key, value in update_data.items():
        setattr(model, key, value)
    await db.commit()
    await db.refresh(model)
    if model.provider_id:
        result = await db.execute(
            select(Model).where(Model.id == model.id).options(selectinload(Model.llm_provider))
        )
        model = result.scalar_one()
    return _model_to_response(model)


async def delete_model(db: AsyncSession, model_id: uuid.UUID) -> bool:
    model = await get_model(db, model_id)
    if not model:
        return False
    await db.delete(model)
    await db.commit()
    return True


async def bulk_create_models(db: AsyncSession, data: ModelBulkCreate) -> list[dict]:
    """Create multiple models at once from discovered models."""
    provider = await db.get(LLMProvider, data.provider_id)
    if not provider:
        return []

    created = []
    for item in data.models:
        model = Model(
            provider=provider.provider_type,
            model_name=item.model_name,
            display_name=item.display_name,
            provider_id=data.provider_id,
            context_window=item.context_window,
            input_modalities=item.input_modalities,
            output_modalities=item.output_modalities,
            cost_per_input_token=item.cost_per_input_token,
            cost_per_output_token=item.cost_per_output_token,
        )
        db.add(model)
        created.append(model)

    await db.commit()
    for m in created:
        await db.refresh(m)

    # Load provider for all
    result = await db.execute(
        select(Model)
        .where(Model.id.in_([m.id for m in created]))
        .options(selectinload(Model.llm_provider))
    )
    models = result.scalars().all()
    return [_model_to_response(m) for m in models]


def _model_to_response(model: Model) -> dict:
    """Convert Model ORM instance to response dict with provider_name."""
    return {
        "id": model.id,
        "provider": model.provider,
        "model_name": model.model_name,
        "display_name": model.display_name,
        "base_url": model.base_url,
        "is_default": model.is_default,
        "cost_per_input_token": model.cost_per_input_token,
        "cost_per_output_token": model.cost_per_output_token,
        "provider_id": model.provider_id,
        "provider_name": model.llm_provider.name if model.llm_provider else None,
        "context_window": model.context_window,
        "input_modalities": model.input_modalities,
        "output_modalities": model.output_modalities,
        "created_at": model.created_at,
    }
