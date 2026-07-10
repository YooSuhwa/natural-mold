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
from app.services.model_metadata import enrich_model


async def resolve_model(db: AsyncSession, model_name: str, *, strict: bool = False) -> Model | None:
    """Look up a ``Model`` row by display name or ``provider:model_name``.

    ``strict=True`` skips the default-model fallback so the caller can detect
    "user asked for a specific model that doesn't exist" cases.

    Tolerant of duplicate ``Model`` rows (display_name / model_name lack a DB
    unique constraint, and ``is_default`` is a plain boolean). Each lookup
    selects a deterministic single row — ``is_default`` first, then oldest by
    ``created_at`` — instead of raising ``MultipleResultsFound``. The
    ``provider:model_name`` form matches provider too, so e.g.
    ``openai_compatible:claude-sonnet-4-6`` never collapses onto the
    ``anthropic`` row with the same ``model_name``.
    """

    order = (Model.is_default.desc(), Model.created_at.asc())

    result = await db.execute(
        select(Model).where(Model.display_name == model_name).order_by(*order).limit(1)
    )
    model = result.scalars().first()
    if model:
        return model

    if ":" in model_name:
        provider_part, parsed = model_name.split(":", 1)
        result = await db.execute(
            select(Model)
            .where(Model.provider == provider_part, Model.model_name == parsed)
            .order_by(*order)
            .limit(1)
        )
        model = result.scalars().first()
        if model:
            return model

    if strict:
        return None

    result = await db.execute(
        select(Model).where(Model.is_default.is_(True)).order_by(*order).limit(1)
    )
    return result.scalars().first()


async def list_models(db: AsyncSession, *, include_hidden: bool = False) -> list[dict]:
    """List models with agent_count. Filters hidden rows by default — only
    super_user surfaces (the ``/models`` admin page) should pass
    ``include_hidden=True``."""

    stmt = (
        select(Model, func.count(Agent.id).label("agent_count"))
        .outerjoin(Agent, Agent.model_id == Model.id)
        .group_by(Model.id)
        .order_by(Model.is_default.desc(), Model.display_name)
    )
    if not include_hidden:
        stmt = stmt.where(Model.is_visible.is_(True))
    result = await db.execute(stmt)
    return [serialize_model(row[0], agent_count=row[1]) for row in result.all()]


async def get_model(db: AsyncSession, model_id: uuid.UUID) -> Model | None:
    result = await db.execute(select(Model).where(Model.id == model_id))
    return result.scalar_one_or_none()


def serialize_model(model: Model, *, agent_count: int = 0) -> dict:
    payload: dict = {
        "id": model.id,
        "provider": model.provider,
        "model_name": model.model_name,
        "display_name": model.display_name,
        "base_url": model.base_url,
        "is_default": model.is_default,
        "is_visible": model.is_visible,
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
        "default_credential_id": model.default_credential_id,
        "agent_count": agent_count,
        "created_at": model.created_at,
    }
    # Surface catalog-only metadata (rankings, etc.) sparsely. The DB row
    # always wins for the columns above; rankings has no ORM home so we
    # source it from the merged catalog when available.
    try:
        enriched = enrich_model(f"{model.provider}/{model.model_name}")
        rankings = enriched.get("rankings")
        if rankings:
            payload["rankings"] = rankings
    except Exception:  # noqa: BLE001, S110 — enrichment is best-effort
        pass
    return payload


__all__ = ["get_model", "list_models", "resolve_model", "serialize_model"]
