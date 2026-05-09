"""Model catalog endpoints — CRUD + discovery glue.

The Credential domain owns provider keys, so model rows are plain reference
data. M7 reintroduces full CRUD on top of the read-only catalog M5 left
behind so the frontend can register/remove/override pricing for newly
discovered or hand-typed model identifiers.

Endpoints:
- ``GET    /api/models``                  list (with ``agent_count``)
- ``GET    /api/models/{id}``             single
- ``POST   /api/models``                  register
- ``PATCH  /api/models/{id}``             update (pricing/meta override)
- ``DELETE /api/models/{id}``             delete (refused if any agent uses it)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    require_super_user,
    verify_csrf,
)
from app.error_codes import model_not_found
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.model import Model
from app.schemas.model import (
    ModelCreate,
    ModelTestPreviewRequest,
    ModelTestResponse,
    ModelUpdate,
)
from app.services import model_service
from app.services.credential_resolver import resolve_credential_for_model
from app.services.model_service import serialize_model
from app.services.model_test import run_model_test

router = APIRouter(prefix="/api/models", tags=["models"])


# Single source of truth for the model wire shape lives in
# ``app.services.model_service.serialize_model`` — keeping list/single/POST
# response shapes in lock-step automatically picks up new ORM columns
# (e.g. ``default_credential_id``) without requiring two separate edits.


@router.get("")
async def list_models(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await model_service.list_models(db)


@router.get("/{model_id}")
async def get_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    model = await model_service.get_model(db, model_id)
    if not model:
        raise model_not_found()
    return serialize_model(model)


@router.post("", status_code=201)
async def create_model(
    payload: ModelCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
):
    """Register a new model row.

    Catalog mutations are operator-only — the model table is a global
    resource and end users should not be able to inject pricing or rebind
    providers. 409 on duplicate ``(provider, model_name)`` so the frontend
    can collapse "already registered" into a friendly toast.
    """

    model = Model(
        provider=payload.provider,
        model_name=payload.model_name,
        display_name=payload.display_name,
        base_url=payload.base_url,
        is_default=payload.is_default,
        cost_per_input_token=payload.cost_per_input_token,
        cost_per_output_token=payload.cost_per_output_token,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        input_modalities=payload.input_modalities,
        output_modalities=payload.output_modalities,
        supports_vision=payload.supports_vision,
        supports_function_calling=payload.supports_function_calling,
        supports_reasoning=payload.supports_reasoning,
        source=payload.source,
        default_credential_id=payload.default_credential_id,
    )
    db.add(model)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"model '{payload.provider}:{payload.model_name}' already exists"
            ),
        ) from exc

    # Pre-existing duplicate detection beyond the unique-index path: an explicit
    # SELECT before INSERT would race; we let the DB win and translate above.
    await db.commit()
    await db.refresh(model)
    return serialize_model(model)


@router.patch("/{model_id}")
async def update_model(
    model_id: uuid.UUID,
    payload: ModelUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
):
    model = await model_service.get_model(db, model_id)
    if not model:
        raise model_not_found()

    updated = payload.model_dump(exclude_unset=True)
    for key, value in updated.items():
        setattr(model, key, value)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="model update would violate the (provider, model_name) uniqueness",
        ) from exc

    await db.commit()
    await db.refresh(model)
    return serialize_model(model)


@router.delete("/{model_id}", status_code=204)
async def delete_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
):
    model = await model_service.get_model(db, model_id)
    if not model:
        raise model_not_found()

    # Refuse if any agent currently points at this model — the FK is non-null
    # and the user almost never wants the cascade.
    in_use = await db.execute(
        select(func.count(Agent.id)).where(Agent.model_id == model_id)
    )
    count = in_use.scalar_one() or 0
    if count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"model is used by {count} agent(s); rebind them before deleting",
        )

    await db.delete(model)
    await db.commit()
    return None


# -- Test surface ------------------------------------------------------------


async def _load_owned_credential(
    db: AsyncSession, credential_id: uuid.UUID, user_id: uuid.UUID
) -> Credential:
    cred = await credential_service.get_for_user(db, credential_id, user_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="credential not found")
    return cred


def _request_meta(request: Request) -> tuple[str | None, str | None]:
    client = request.client.host if request.client else None
    return client, request.headers.get("user-agent")


@router.post("/{model_id}/test", response_model=ModelTestResponse)
async def test_registered_model(
    model_id: uuid.UUID,
    request: Request,
    credential_id: uuid.UUID | None = Query(
        None,
        description=(
            "Stored credential whose decrypted payload supplies the API key. "
            "If omitted, falls back to the model's default_credential_id "
            "(captured at Add-model time)."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ModelTestResponse:
    """Probe a registered ``Model`` row using a Credential.

    Tiered credential lookup: explicit ``credential_id`` query param > model's
    ``default_credential_id``. 422 if neither resolves (user has no usable
    credential for this model).
    """

    model = await model_service.get_model(db, model_id)
    if model is None:
        raise model_not_found()

    cred = await resolve_credential_for_model(db, model, credential_id, user.id)
    if cred is None:
        # Disambiguate so existing 404-on-invalid-credential clients keep
        # working while still giving a useful 422 when the user simply has
        # no default bound.
        if credential_id is not None:
            raise HTTPException(status_code=404, detail="credential not found")
        raise HTTPException(
            status_code=422,
            detail=(
                "no usable credential — pass credential_id or set the model's "
                "default_credential_id."
            ),
        )
    data = await credential_service.decrypt_with_external(cred.data_encrypted)

    result = await run_model_test(
        provider=model.provider,
        model_name=model.model_name,
        base_url=model.base_url,
        credential_data=data,
        cost_per_input_token=model.cost_per_input_token,
        cost_per_output_token=model.cost_per_output_token,
    )

    payload = result.to_dict()
    ip, user_agent = _request_meta(request)
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=user.id,
        action="test",
        source="api",
        ip=ip,
        user_agent=user_agent,
        metadata={
            "model_id": str(model.id),
            "provider": model.provider,
            "model_name": model.model_name,
            "success": payload["success"],
        },
        error=None if payload["success"] else (
            payload["error"]["message"] if payload.get("error") else None
        ),
    )
    await db.commit()
    return ModelTestResponse(**payload)


@router.post("/test-preview", response_model=ModelTestResponse)
async def test_preview_model(
    payload: ModelTestPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ModelTestResponse:
    """Probe an unregistered ``provider:model_name`` combo (Custom ID flow).

    Same semantics as ``test_registered_model`` minus the catalog lookup — the
    request body carries the model identity inline so the user can validate a
    pasted-in ID before committing it to the table.
    """

    cred = await _load_owned_credential(db, payload.credential_id, user.id)
    data = await credential_service.decrypt_with_external(cred.data_encrypted)

    result = await run_model_test(
        provider=payload.provider,
        model_name=payload.model_name,
        base_url=payload.base_url,
        credential_data=data,
    )

    body = result.to_dict()
    ip, user_agent = _request_meta(request)
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=user.id,
        action="test",
        source="api",
        ip=ip,
        user_agent=user_agent,
        metadata={
            "preview": True,
            "provider": payload.provider,
            "model_name": payload.model_name,
            "success": body["success"],
        },
        error=None if body["success"] else (
            body["error"]["message"] if body.get("error") else None
        ),
    )
    await db.commit()
    return ModelTestResponse(**body)
