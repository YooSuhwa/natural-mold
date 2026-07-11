"""Model catalog service — lookup helpers + operator CRUD (BE-S2).

The greenfield ``Model`` ORM no longer carries provider keys (those live in
``Credential``). Lookup helpers serve Builder / Assistant; the CRUD half backs
:mod:`app.routers.models`, which keeps HTTP concerns only (schema conversion,
``Depends`` guards, commits).

Transaction policy: the service ``flush``es, the calling router ``commit``s.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.model import Model
from app.schemas.model import ModelCreate, ModelUpdate
from app.services import audit_service
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


# -- Operator CRUD (BE-S2) -------------------------------------------------------


async def create_model(db: AsyncSession, *, data: ModelCreate) -> Model:
    """Register a new model row.

    409 on duplicate ``(provider, model_name)`` — an explicit SELECT before
    INSERT would race; we let the DB win and translate the IntegrityError.
    """

    if data.is_default and not data.is_visible:
        raise HTTPException(
            status_code=422,
            detail="cannot mark a hidden model as default",
        )

    model = Model(
        provider=data.provider,
        model_name=data.model_name,
        display_name=data.display_name,
        base_url=data.base_url,
        is_default=data.is_default,
        is_visible=data.is_visible,
        cost_per_input_token=data.cost_per_input_token,
        cost_per_output_token=data.cost_per_output_token,
        context_window=data.context_window,
        max_output_tokens=data.max_output_tokens,
        input_modalities=data.input_modalities,
        output_modalities=data.output_modalities,
        supports_vision=data.supports_vision,
        supports_function_calling=data.supports_function_calling,
        supports_reasoning=data.supports_reasoning,
        source=data.source,
        default_credential_id=data.default_credential_id,
    )
    db.add(model)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Session-wide rollback: callers must not stack earlier pending
        # changes on this session before calling (single-mutation requests
        # only) — wrap in begin_nested() if that ever changes.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(f"model '{data.provider}:{data.model_name}' already exists"),
        ) from exc
    return model


async def update_model(db: AsyncSession, *, model: Model, data: ModelUpdate) -> list[str]:
    """Apply a partial update. Returns the changed field names (for audit)."""

    updated = data.model_dump(exclude_unset=True)
    # is_default 와 is_visible 의 최종 조합이 모순(기본인데 숨김)이면 거부.
    final_default = updated.get("is_default", model.is_default)
    final_visible = updated.get("is_visible", model.is_visible)
    if final_default and not final_visible:
        raise HTTPException(
            status_code=422,
            detail="cannot mark a hidden model as default",
        )
    for key, value in updated.items():
        setattr(model, key, value)

    try:
        await db.flush()
    except IntegrityError as exc:
        # Session-wide rollback — same single-mutation caller contract as
        # create_model above.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="model update would violate the (provider, model_name) uniqueness",
        ) from exc
    return sorted(updated.keys())


async def ensure_model_unused(db: AsyncSession, model_id: uuid.UUID) -> None:
    """Refuse deletion while any agent points at this model — the FK is
    non-null and the user almost never wants the cascade."""

    in_use = await db.execute(select(func.count(Agent.id)).where(Agent.model_id == model_id))
    count = in_use.scalar_one() or 0
    if count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"model is used by {count} agent(s); rebind them before deleting",
        )


async def delete_model(db: AsyncSession, *, model: Model) -> None:
    await db.delete(model)
    await db.flush()


# -- Side effects ----------------------------------------------------------------


def model_metadata(model: Model) -> dict[str, object]:
    return {
        "provider": model.provider,
        "model_name": model.model_name,
        "display_name": model.display_name,
        "source": model.source,
        "is_default": model.is_default,
        "is_visible": model.is_visible,
        "has_base_url": bool(model.base_url),
        "default_credential_bound": model.default_credential_id is not None,
    }


async def record_model_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    model: Model,
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
        target_type="model",
        target_id=model.id,
        target_name_snapshot=model.display_name or model.model_name,
        target_owner_user_id=None,
        outcome="success",
        request=request,
        metadata={**model_metadata(model), **(metadata or {})},
    )


__all__ = [
    "create_model",
    "delete_model",
    "ensure_model_unused",
    "get_model",
    "list_models",
    "model_metadata",
    "record_model_audit",
    "resolve_model",
    "serialize_model",
    "update_model",
]
