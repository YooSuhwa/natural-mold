"""Organization catalog policy metadata and versioned governance writes."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.authz.org_control import OrgControlContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import verify_csrf
from app.routers.orgs import mutation_request, org_policy_context
from app.schemas.org_catalog import CatalogPolicy, CatalogPolicyView
from app.services.orgs.catalog import read_catalog_policy, update_catalog_policy
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
) -> JSONResponse:
    result = await update_catalog_policy(mutation, context, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
