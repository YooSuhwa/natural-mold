"""Administrative role and capability deltas with CSRF and optimistic versions."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.authz.membership_apply import apply_for_request
from app.authz.org_control import OrgControlContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import verify_csrf
from app.routers.orgs import mutation_request, org_members_context
from app.schemas.org_assignments import (
    CapabilityAssignment,
    CapabilityGrantView,
    GrantDirectoryQuery,
    RoleAssignment,
    RoleGrantView,
)
from app.schemas.org_groups import DirectoryPage
from app.services.orgs import capabilities, roles
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.versioning import etag

router = APIRouter(prefix="/api/orgs/{org_id}", tags=["organization assignments"])


@router.get("/roles", response_model=DirectoryPage[RoleGrantView])
@authz_policy(SELF)
async def list_roles(
    query: Annotated[GrantDirectoryQuery, Query()],
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await roles.list_roles(mutation.db, context, query)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.put(
    "/roles", response_model=DirectoryPage[RoleGrantView], dependencies=[Depends(verify_csrf)]
)
@authz_policy(SELF)
async def replace_roles(
    payload: RoleAssignment,
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await roles.replace_roles(mutation, context, payload)
    await apply_for_request(mutation.request, mutation.db)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.get("/capabilities", response_model=DirectoryPage[CapabilityGrantView])
@authz_policy(SELF)
async def list_capabilities(
    query: Annotated[GrantDirectoryQuery, Query()],
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await capabilities.list_capabilities(mutation.db, context, query)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.put(
    "/capabilities",
    response_model=DirectoryPage[CapabilityGrantView],
    dependencies=[Depends(verify_csrf)],
)
@authz_policy(SELF)
async def replace_capabilities(
    payload: CapabilityAssignment,
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await capabilities.replace_capabilities(mutation, context, payload)
    await apply_for_request(mutation.request, mutation.db)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
