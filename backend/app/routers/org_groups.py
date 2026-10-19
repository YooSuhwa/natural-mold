"""Organization group directory and manual lifecycle operations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from app.authz.membership_apply import apply_for_request
from app.authz.org_control import OrgControlContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import verify_csrf
from app.routers.orgs import mutation_request, org_members_context, org_read_context
from app.schemas.org_groups import DirectoryPage, DirectoryQuery, GroupCreate, GroupPatch, GroupView
from app.services.orgs import groups
from app.services.orgs.groups import GroupMutationRequest
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.versioning import etag

router = APIRouter(prefix="/api/orgs/{org_id}/groups", tags=["organization groups"])


async def group_mutation_request(
    group_id: UUID,
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> GroupMutationRequest:
    return GroupMutationRequest(request=mutation, group_id=group_id)


@router.get("", response_model=DirectoryPage[GroupView])
@authz_policy(SELF)
async def list_groups(
    query: Annotated[DirectoryQuery, Query()],
    context: OrgControlContext = Depends(org_read_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await groups.list_groups(mutation.db, context, query)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.get("/{group_id}", response_model=GroupView)
@authz_policy(SELF)
async def get_group(
    context: OrgControlContext = Depends(org_read_context),
    mutation: GroupMutationRequest = Depends(group_mutation_request),
) -> JSONResponse:
    result = await groups.get_group(mutation.request.db, context, mutation.group_id)
    return JSONResponse(
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )


@router.post("", response_model=GroupView, status_code=201, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def create_group(
    payload: GroupCreate,
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await groups.create_group(mutation, context, payload)
    await apply_for_request(mutation.request, mutation.db)
    return JSONResponse(
        status_code=201,
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )


@router.patch("/{group_id}", response_model=GroupView, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def update_group(
    payload: GroupPatch,
    context: OrgControlContext = Depends(org_members_context),
    mutation: GroupMutationRequest = Depends(group_mutation_request),
) -> JSONResponse:
    result = await groups.update_group(mutation, context, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )


@router.delete("/{group_id}", status_code=204, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def delete_group(
    context: OrgControlContext = Depends(org_members_context),
    mutation: GroupMutationRequest = Depends(group_mutation_request),
) -> Response:
    await groups.delete_group(mutation, context)
    await apply_for_request(mutation.request.request, mutation.request.db)
    return Response(status_code=204)
