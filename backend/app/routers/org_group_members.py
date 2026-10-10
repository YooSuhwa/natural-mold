"""Scoped manual group rosters and membership mutations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from app.authz.org_control import OrgControlContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import verify_csrf
from app.routers.org_groups import group_mutation_request
from app.routers.orgs import org_members_context, org_read_context
from app.schemas.org_groups import DirectoryPage, DirectoryQuery, GroupMemberCreate, GroupMemberView
from app.services.orgs import group_members
from app.services.orgs.groups import GroupMutationRequest
from app.services.orgs.versioning import etag

router = APIRouter(
    prefix="/api/orgs/{org_id}/groups/{group_id}/members", tags=["organization group members"]
)


@router.get("", response_model=DirectoryPage[GroupMemberView])
@authz_policy(SELF)
async def list_group_members(
    query: Annotated[DirectoryQuery, Query()],
    context: OrgControlContext = Depends(org_read_context),
    mutation: GroupMutationRequest = Depends(group_mutation_request),
) -> JSONResponse:
    result = await group_members.list_group_members(mutation, context, query)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.post(
    "", response_model=GroupMemberView, status_code=201, dependencies=[Depends(verify_csrf)]
)
@authz_policy(SELF)
async def add_group_member(
    payload: GroupMemberCreate,
    context: OrgControlContext = Depends(org_members_context),
    mutation: GroupMutationRequest = Depends(group_mutation_request),
) -> JSONResponse:
    result, created = await group_members.add_group_member(mutation, context, payload)
    return JSONResponse(
        status_code=201 if created else 200,
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )


@router.delete("/{user_id}", status_code=204, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def remove_group_member(
    user_id: UUID,
    context: OrgControlContext = Depends(org_members_context),
    mutation: GroupMutationRequest = Depends(group_mutation_request),
) -> Response:
    await group_members.remove_group_member(mutation, context, user_id)
    return Response(status_code=204)
