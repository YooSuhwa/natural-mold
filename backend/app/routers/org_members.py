"""Organization membership administration with recovery and versioned status changes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.delegation_links import DelegationScope
from app.authz.delegation_sweep import revalidate_after
from app.authz.member_exit_preview import member_exit_preview
from app.authz.org_control import OrgControlContext
from app.authz.policies import SELF, authz_policy
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import get_db, verify_csrf
from app.routers.orgs import mutation_request, org_members_context, org_read_context
from app.schemas.org_groups import DirectoryPage, DirectoryQuery
from app.schemas.org_member_exit import MemberExitPreview, MemberRemoval, MemberRemovalResult
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


async def _validate_delegations(
    mutation: MemberMutationRequest, context: OrgControlContext, authz: AuthzService
) -> None:
    """10.7 item (5): the leaver's delegations are re-checked after the exit applied."""
    scope = DelegationScope(context.organization.id, delegators=frozenset({mutation.user_id}))
    tenant_id = context.organization.tenant_id
    await revalidate_after(authz, scope, tenant_id, trigger="member_exit")


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


@router.get("/{user_id}/exit-preview", response_model=MemberExitPreview)
@authz_policy(SELF)
async def preview_member_exit(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgControlContext = Depends(org_members_context),
    authz: AuthzService = Depends(get_authz),
) -> MemberExitPreview:
    """10.7 / S15 steps (1)-(2): what removing the member settles and stops."""
    return await member_exit_preview(db, context, user_id, authz)


@router.post(
    "/{user_id}/removal", response_model=MemberRemovalResult, dependencies=[Depends(verify_csrf)]
)
@authz_policy(SELF)
async def remove_member_with_settlement(
    payload: MemberRemoval,
    context: OrgControlContext = Depends(org_members_context),
    mutation: MemberMutationRequest = Depends(member_mutation_request),
    authz: AuthzService = Depends(get_authz),
) -> JSONResponse:
    """10.7 / S15: transfer or delete the member's resources, revoke their grants, stop
    their entry points and remove the membership in one transaction."""
    outcome = await members.remove_member(mutation, context, authz, payload)
    await apply_lifecycle(outcome.change, authz)
    await _validate_delegations(mutation, context, authz)
    return JSONResponse(
        content=outcome.view().model_dump(mode="json"),
        headers={"ETag": etag(context.organization.updated_at)},
    )


@router.delete("/{user_id}", status_code=204, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def remove_member(
    context: OrgControlContext = Depends(org_members_context),
    mutation: MemberMutationRequest = Depends(member_mutation_request),
    authz: AuthzService = Depends(get_authz),
) -> Response:
    outcome = await members.remove_member(mutation, context, authz)
    await apply_lifecycle(outcome.change, authz)
    await _validate_delegations(mutation, context, authz)
    return Response(status_code=204)
