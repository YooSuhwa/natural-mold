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

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_item_fanout import commit_item_change
from app.authz.model_access import (
    load_owned_credential,
    model_test_credential,
    require_visible_model,
)
from app.authz.org_context import OrgContext
from app.authz.policies import CANDIDATES, RES, SELF, SUPER, authz_policy
from app.authz.resource_gate import gate_list, org_context_when_consulted
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.credentials.boundary import open_values
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_org_context,
    require_super_user,
    verify_csrf,
)
from app.error_codes import (
    model_not_found,
    super_user_required,
)
from app.schemas.model import (
    ModelCreate,
    ModelTestPreviewRequest,
    ModelTestResponse,
    ModelUpdate,
)
from app.services import model_service
from app.services.model_service import serialize_model
from app.services.model_test import run_model_test
from app.services.model_test_audit import record_model_test

router = APIRouter(prefix="/api/models", tags=["models"])


# Single source of truth for the model wire shape lives in
# ``app.services.model_service.serialize_model`` — keeping list/single/POST
# response shapes in lock-step automatically picks up new ORM columns
# (e.g. ``default_credential_id``) without requiring two separate edits.


@router.get("")
@authz_policy(CANDIDATES("llm_model", "can_use"))
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
    context: OrgContext | None = Depends(org_context_when_consulted),
    authz: AuthzService = Depends(get_authz),
):
    if include_hidden and not user.is_super_user:
        raise super_user_required()
    rows = await model_service.list_models(db, include_hidden=include_hidden, context=context)
    if context is None or include_hidden:  # legacy, or the operator catalog page
        return rows
    return await gate_list(authz, context, "llm_model", "can_use", rows, lambda row: row["id"])


@router.get("/{model_id}")
@authz_policy(RES("llm_model", "can_use", param="model_id", min_visible="can_use"))
async def get_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext | None = Depends(org_context_when_consulted),
    authz: AuthzService = Depends(get_authz),
):
    model = await model_service.load_model(db, model_id, context, authz)
    return serialize_model(require_visible_model(model, user))


@router.post("", status_code=201)
@authz_policy(SUPER)
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
    await commit_item_change(db, request, user.id, "model", model.id)  # E-03 fanout
    return serialize_model(model)


@router.patch("/{model_id}")
@authz_policy(SUPER)
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
    await commit_item_change(db, request, user.id, "model", model_id)  # E-03 fanout
    return serialize_model(model)


@router.delete("/{model_id}", status_code=204)
@authz_policy(SUPER)
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
    await commit_item_change(db, request, user.id, "model", model_id)  # E-03 fanout
    return


# -- Test surface ------------------------------------------------------------


@router.post("/{model_id}/test", response_model=ModelTestResponse)
@authz_policy(RES("llm_model", "can_use", param="model_id", min_visible="can_use"))
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
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> ModelTestResponse:
    """Probe a registered ``Model`` row using a Credential.

    Tiered credential lookup: explicit ``credential_id`` query param > model's
    ``default_credential_id``. 422 if neither resolves (user has no usable
    credential for this model).
    """

    model = require_visible_model(
        await model_service.load_model(db, model_id, context, authz), user
    )
    cred = await model_test_credential(db, model, credential_id, context, authz)
    data = await open_values(cred, "probe")

    result = await run_model_test(
        provider=model.provider,
        model_name=model.model_name,
        base_url=model.base_url,
        credential_data=data,
        cost_per_input_token=model.cost_per_input_token,
        cost_per_output_token=model.cost_per_output_token,
    )

    payload = result.to_dict()
    await record_model_test(
        db,
        request,
        credential_id=cred.id,
        actor_user_id=user.id,
        payload=payload,
        metadata={
            "model_id": str(model.id),
            "provider": model.provider,
            "model_name": model.model_name,
        },
    )
    await db.commit()
    return ModelTestResponse(**payload)


@router.post("/test-preview", response_model=ModelTestResponse)
@authz_policy(SELF)
async def test_preview_model(
    payload: ModelTestPreviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> ModelTestResponse:
    """Probe an unregistered ``provider:model_name`` combo (Custom ID flow).

    Same semantics as ``test_registered_model`` minus the catalog lookup — the
    request body carries the model identity inline so the user can validate a
    pasted-in ID before committing it to the table.
    """

    cred = await load_owned_credential(db, payload.credential_id, context, authz)
    data = await open_values(cred, "probe")

    result = await run_model_test(
        provider=payload.provider,
        model_name=payload.model_name,
        base_url=payload.base_url,
        credential_data=data,
    )

    body = result.to_dict()
    await record_model_test(
        db,
        request,
        credential_id=cred.id,
        actor_user_id=user.id,
        payload=body,
        metadata={
            "preview": True,
            "provider": payload.provider,
            "model_name": payload.model_name,
        },
    )
    await db.commit()
    return ModelTestResponse(**body)
