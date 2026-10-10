"""Organization membership administration with recovery and versioned status changes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from app.authz.org_control import OrgControlContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import verify_csrf
from app.routers.orgs import mutation_request, org_members_context, org_read_context
from app.schemas.org_groups import DirectoryPage, DirectoryQuery
from app.schemas.org_members import MemberCreate, MemberPatch, MemberView
from app.services.orgs import members
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.members import MemberMutationRequest
from app.services.orgs.versioning import etag

router = APIRouter(prefix="/api/orgs/{org_id}/members", tags=["organization members"])


async def member_mutation_request(
    user_id: UUID,
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> MemberMutationRequest:
    return MemberMutationRequest(request=mutation, user_id=user_id)


@router.get("", response_model=DirectoryPage[MemberView])
@authz_policy(SELF)
async def list_members(
    query: Annotated[DirectoryQuery, Query()],
    context: OrgControlContext = Depends(org_read_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await members.list_members(mutation, context, query)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.post("", response_model=MemberView, status_code=201, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def create_member(
    payload: MemberCreate,
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result, created = await members.create_member(mutation, context, payload)
    return JSONResponse(
        status_code=201 if created else 200,
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )


@router.patch("/{user_id}", response_model=MemberView, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def change_member_status(
    payload: MemberPatch,
    context: OrgControlContext = Depends(org_members_context),
    mutation: MemberMutationRequest = Depends(member_mutation_request),
) -> JSONResponse:
    result = await members.change_member_status(mutation, context, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )


@router.delete("/{user_id}", status_code=204, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def remove_member(
    context: OrgControlContext = Depends(org_members_context),
    mutation: MemberMutationRequest = Depends(member_mutation_request),
) -> Response:
    await members.remove_member(mutation, context)
    return Response(status_code=204)
