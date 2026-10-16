"""Model route checks (plan 9.5, D-04/D-10): catalog visibility, target and credential.

Each check runs the route's existing loader as the legacy answer through ``gate_row``
(same 404 objects; ``llm_model``/``credential#can_use`` when OpenFGA is consulted).
"""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.resource_gate import CREDENTIAL, LLM_MODEL, gate_row
from app.authz.service import AuthzService
from app.credentials import service as credential_service
from app.dependencies import CurrentUser
from app.error_codes import credential_not_found, model_not_found
from app.models.credential import Credential
from app.models.model import Model
from app.services.credential_resolver import resolve_credential_for_model


def require_company_model(model: Model, tenant_id: UUID | None) -> Model:
    """Platform rows or the caller company's own models, in every mode (E-07)."""
    if model.tenant_id is not None and model.tenant_id != tenant_id:
        raise model_not_found()
    return model


def require_visible_model(model: Model, user: CurrentUser) -> Model:
    """The model list's rows, in every mode: hidden models are operator-only (D-10)."""
    if not (model.is_visible or user.is_super_user):
        raise model_not_found()
    return model


async def load_model_target(
    db: AsyncSession, authz: AuthzService, context: OrgContext, model_id: UUID, user: CurrentUser
) -> Model:
    """A visible catalog row by id (health history/check); else ``MODEL_NOT_FOUND``."""

    async def legacy() -> Model:
        row = await db.get(Model, model_id)
        if row is None:
            raise model_not_found()
        return require_company_model(row, context.tenant_id)

    model = await gate_row(authz, db, context, LLM_MODEL, model_id, "can_use", legacy)
    return require_visible_model(require_company_model(model, context.tenant_id), user)


async def load_owned_credential(
    db: AsyncSession, credential_id: UUID, context: OrgContext, authz: AuthzService
) -> Credential:
    """The caller's own credential (``credential#can_use`` when consulted)."""

    async def legacy() -> Credential:
        cred = await credential_service.get_for_user(db, credential_id, context)
        if cred is None:
            raise credential_not_found()
        return cred

    return await gate_row(authz, db, context, CREDENTIAL, credential_id, "can_use", legacy)


async def model_test_credential(
    db: AsyncSession,
    model: Model,
    credential_id: UUID | None,
    context: OrgContext,
    authz: AuthzService,
) -> Credential:
    """Explicit id: owner scope + ``can_use`` (D-10); else the model's owner-checked default."""
    if credential_id is not None:
        return await load_owned_credential(db, credential_id, context, authz)
    cred = await resolve_credential_for_model(db, model, None, context)
    if cred is None:  # a useful 422 when the user simply has no default bound
        raise HTTPException(
            status_code=422,
            detail=(
                "no usable credential — pass credential_id or set the model's "
                "default_credential_id."
            ),
        )
    return cred
