"""Logged-in invitation acceptance uses CSRF and never returns a token or email."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import set_active_org_cookie
from app.authz.membership_apply import apply_for_request
from app.authz.policies import SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_identity_db, verify_csrf
from app.routers.orgs import mutation_request
from app.schemas.invitation_accept import InvitationAccept, InvitationAccepted
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.invitation_acceptance import InvitationAcceptance, accept_invitation
from app.services.tenants.invitation_acceptance import (
    TenantInvitationAcceptance,
    accept_tenant_invitation,
)

router = APIRouter(prefix="/api/invitations", tags=["invitation acceptance"])


async def authenticated_acceptance(
    payload: InvitationAccept,
    user: CurrentUser = Depends(get_current_user),
    _identity: AsyncSession = Depends(get_identity_db),  # I-06: joins a company's directory
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> InvitationAccepted:
    token = payload.token.get_secret_value()
    if token.startswith("tv1."):
        result = await accept_tenant_invitation(
            TenantInvitationAcceptance(mutation, user.id, payload.token)
        )
    else:
        result = await accept_invitation(InvitationAcceptance(mutation, user.id, payload.token))
    await mutation.db.commit()
    await apply_for_request(mutation.request, mutation.db)
    return result


@router.post("/accept", response_model=InvitationAccepted, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def accept_invitation_link(
    result: InvitationAccepted = Depends(authenticated_acceptance),
) -> JSONResponse:
    response = JSONResponse(content=result.model_dump(mode="json"))
    if result.org_id is not None:
        set_active_org_cookie(response, result.org_id)
    return response
