"""Minimal model lookup helpers used by Builder / Assistant.

The greenfield ``Model`` ORM no longer carries provider keys (those live in
``Credential``); CRUD goes through the new tools/credentials surfaces. This
module keeps just enough of the legacy public API for the builder/assistant
flow to stay functional through M5 — full removal lands in M6 with the
frontend rewrite.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.model import Model


async def resolve_model(
    db: AsyncSession, model_name: str, *, strict: bool = False
) -> Model | None:
    """Look up a ``Model`` row by display name or ``provider:model_name``.

    ``strict=True`` skips the default-model fallback so the caller can detect
    "user asked for a specific model that doesn't exist" cases.
    """

    result = await db.execute(select(Model).where(Model.display_name == model_name))
    model = result.scalar_one_or_none()
    if model:
        return model

    if ":" in model_name:
        _, parsed = model_name.split(":", 1)
        result = await db.execute(select(Model).where(Model.model_name == parsed))
        model = result.scalar_one_or_none()
        if model:
            return model

    if strict:
        return None

    result = await db.execute(select(Model).where(Model.is_default.is_(True)))
    return result.scalar_one_or_none()


async def list_models(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Model, func.count(Agent.id).label("agent_count"))
        .outerjoin(Agent, Agent.model_id == Model.id)
        .group_by(Model.id)
        .order_by(Model.is_default.desc(), Model.display_name)
    )
    return [_serialize(row[0], agent_count=row[1]) for row in result.all()]


async def get_model(db: AsyncSession, model_id: uuid.UUID) -> Model | None:
    result = await db.execute(select(Model).where(Model.id == model_id))
    return result.scalar_one_or_none()


def _serialize(model: Model, *, agent_count: int = 0) -> dict:
    return {
        "id": model.id,
        "provider": model.provider,
        "model_name": model.model_name,
        "display_name": model.display_name,
        "base_url": model.base_url,
        "is_default": model.is_default,
        "cost_per_input_token": model.cost_per_input_token,
        "cost_per_output_token": model.cost_per_output_token,
        "context_window": model.context_window,
        "max_output_tokens": model.max_output_tokens,
        "input_modalities": model.input_modalities,
        "output_modalities": model.output_modalities,
        "supports_vision": model.supports_vision,
        "supports_function_calling": model.supports_function_calling,
        "supports_reasoning": model.supports_reasoning,
        "source": model.source,
        "agent_count": agent_count,
        "created_at": model.created_at,
    }


__all__ = ["get_model", "list_models", "resolve_model"]
