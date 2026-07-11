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
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    require_super_user,
    verify_csrf,
)
from app.error_codes import (
    credential_not_found,
    model_not_found,
    super_user_required,
)
from app.models.credential import Credential
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
    include_hidden: bool = Query(
        False,
        description=(
            "Super-user only — include rows where ``is_visible=False``. "
            "Drives the operator-facing ``/models`` admin page."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if include_hidden and not user.is_super_user:
        raise super_user_required()
    return await model_service.list_models(db, include_hidden=include_hidden)


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
    request: Request,
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

    model = await model_service.create_model(db, data=payload)
    await db.commit()
    await db.refresh(model)
    await model_service.record_model_audit(
        db,
        user=user,
        request=request,
        action="model.create",
        model=model,
    )
    await db.commit()
    return serialize_model(model)


@router.patch("/{model_id}")
async def update_model(
    model_id: uuid.UUID,
    payload: ModelUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
):
    model = await model_service.get_model(db, model_id)
    if not model:
        raise model_not_found()

    changed_fields = await model_service.update_model(db, model=model, data=payload)
    await db.commit()
    await db.refresh(model)
    await model_service.record_model_audit(
        db,
        user=user,
        request=request,
        action="model.update",
        model=model,
        metadata={"changed_fields": changed_fields},
    )
    await db.commit()
    return serialize_model(model)


@router.delete("/{model_id}", status_code=204)
async def delete_model(
    model_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
):
    model = await model_service.get_model(db, model_id)
    if not model:
        raise model_not_found()

    await model_service.ensure_model_unused(db, model_id)
    await model_service.record_model_audit(
        db,
        user=user,
        request=request,
        action="model.delete",
        model=model,
    )
    await model_service.delete_model(db, model=model)
    await db.commit()
    return


# -- Test surface ------------------------------------------------------------


async def _load_owned_credential(
    db: AsyncSession, credential_id: uuid.UUID, user_id: uuid.UUID
) -> Credential:
    cred = await credential_service.get_for_user(db, credential_id, user_id)
    if cred is None:
        raise credential_not_found()
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
            raise credential_not_found()
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
        error=None
        if payload["success"]
        else (payload["error"]["message"] if payload.get("error") else None),
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
        error=None
        if body["success"]
        else (body["error"]["message"] if body.get("error") else None),
    )
    await db.commit()
    return ModelTestResponse(**body)
