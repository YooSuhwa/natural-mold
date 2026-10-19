"""Model test runs: decide, then probe (plan 11.0 "모델 시험·설정 시험 대화"; F-13).

``run_model_test`` sends a real request with decrypted credential values, so it is only
called here, after both decisions of the request that asked for it:

* the registered model: the catalog gate (``load_model``: ``llm_model#can_use`` when
  consulted) and the list's visibility rule (hidden rows are operator-only);
* the credential: an explicit one through the owner-scoped ``credential#can_use`` gate,
  the model's default the same way outside legacy (``model_test_credential``); a
  preview's body credential through that gate too.

A revoked permission is refused before any value is decrypted or sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.model_access import (
    load_owned_credential,
    model_test_credential,
    require_visible_model,
)
from app.authz.org_context import OrgContext
from app.authz.service import AuthzService
from app.credentials.boundary import open_values
from app.dependencies import CurrentUser
from app.schemas.model import ModelTestPreviewRequest
from app.services import model_service
from app.services.model_test import run_model_test


@dataclass(frozen=True, slots=True)
class ModelProbe:
    credential_id: UUID
    payload: dict[str, Any]
    metadata: dict[str, Any]  # the probe audit row's (``record_model_test``)


async def probe_registered_model(
    db: AsyncSession,
    model_id: UUID,
    credential_id: UUID | None,
    *,
    user: CurrentUser,
    context: OrgContext,
    authz: AuthzService,
) -> ModelProbe:
    model = require_visible_model(
        await model_service.load_model(db, model_id, context, authz), user
    )
    cred = await model_test_credential(db, model, credential_id, context, authz)
    result = await run_model_test(
        provider=model.provider,
        model_name=model.model_name,
        base_url=model.base_url,
        credential_data=await open_values(cred, "probe"),
        cost_per_input_token=model.cost_per_input_token,
        cost_per_output_token=model.cost_per_output_token,
    )
    metadata = {
        "model_id": str(model.id),
        "provider": model.provider,
        "model_name": model.model_name,
    }
    return ModelProbe(cred.id, result.to_dict(), metadata)


async def probe_preview_model(
    db: AsyncSession,
    payload: ModelTestPreviewRequest,
    *,
    context: OrgContext,
    authz: AuthzService,
) -> ModelProbe:
    cred = await load_owned_credential(db, payload.credential_id, context, authz)
    result = await run_model_test(
        provider=payload.provider,
        model_name=payload.model_name,
        base_url=payload.base_url,
        credential_data=await open_values(cred, "probe"),
    )
    metadata = {"preview": True, "provider": payload.provider, "model_name": payload.model_name}
    return ModelProbe(cred.id, result.to_dict(), metadata)


__all__ = ["ModelProbe", "probe_preview_model", "probe_registered_model"]
