"""Organization catalog policy metadata, impact preview and versioned governance writes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.policies import SELF, authz_policy
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.orgs import mutation_request, org_policy_context
from app.schemas.org_catalog import CatalogImpactView, CatalogKind, CatalogPolicy, CatalogPolicyView
from app.services.orgs.catalog import (
    catalog_policy_impact,
    read_catalog_policy,
    update_catalog_policy,
)
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.versioning import etag

router = APIRouter(prefix="/api/orgs/{org_id}/catalog-policy", tags=["organization catalog"])


@router.get("", response_model=CatalogPolicyView)
@authz_policy(SELF)
async def get_catalog_policy(
    context: OrgControlContext = Depends(org_policy_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await read_catalog_policy(mutation, context)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.put("", response_model=CatalogPolicyView, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def put_catalog_policy(
    payload: CatalogPolicy,
    context: OrgControlContext = Depends(org_policy_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
    authz: AuthzService = Depends(get_authz),
) -> JSONResponse:
    result, change = await update_catalog_policy(mutation, context, payload, mode=authz.mode)
    await apply_lifecycle(change, authz)  # 8.6 item 2: before responding
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


async def org_policy_read_context(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgControlContext:
    """``can_manage_policy`` without locking the organization row (read-only preview)."""
    target = OrgControlTarget(org_id, "can_manage_policy", lock_organization=False)
    return await load_org_control(db, user, target)


@router.get("/impact", response_model=CatalogImpactView)
@authz_policy(SELF)
async def get_catalog_policy_impact(
    item_type: CatalogKind = Query(alias="type"),
    item_id: UUID = Query(alias="id"),
    context: OrgControlContext = Depends(org_policy_read_context),
    db: AsyncSession = Depends(get_db),
) -> CatalogImpactView:
    """Agents of this organization affected by turning one item off (S09 confirmation)."""
    return await catalog_policy_impact(db, context, item_type, item_id)
