"""Organization invitations; authenticated acceptance is implemented in B-05."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from app.authz.org_control import OrgControlContext
from app.authz.policies import SELF, authz_policy
from app.dependencies import verify_csrf
from app.routers.orgs import mutation_request, org_members_context
from app.schemas.org_groups import DirectoryPage, DirectoryQuery
from app.schemas.org_invitations import InvitationCreate, InvitationCreated, InvitationView
from app.services.orgs import invitations
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.invitation_resend import resend_invitation
from app.services.orgs.versioning import etag

router = APIRouter(prefix="/api/orgs/{org_id}/invitations", tags=["organization invitations"])


@router.get("", response_model=DirectoryPage[InvitationView])
@authz_policy(SELF)
async def list_invitations(
    query: Annotated[DirectoryQuery, Query()],
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await invitations.list_invitations(mutation, context, query)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )


@router.post(
    "", response_model=InvitationCreated, status_code=201, dependencies=[Depends(verify_csrf)]
)
@authz_policy(SELF)
async def create_invitation(
    payload: InvitationCreate,
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result, created = await invitations.create_invitation(mutation, context, payload)
    return JSONResponse(
        status_code=201 if created else 200,
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )


@router.delete("/{invitation_id}", status_code=204, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def revoke_invitation(
    invitation_id: UUID,
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> Response:
    await invitations.revoke_invitation(mutation, context, invitation_id)
    return Response(status_code=204)


@router.post(
    "/{invitation_id}/resend", response_model=InvitationCreated, dependencies=[Depends(verify_csrf)]
)
@authz_policy(SELF)
async def resend_invitation_link(
    invitation_id: UUID,
    context: OrgControlContext = Depends(org_members_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result, created = await resend_invitation(mutation, context, invitation_id)
    return JSONResponse(
        status_code=201 if created else 200,
        content=result.model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )
