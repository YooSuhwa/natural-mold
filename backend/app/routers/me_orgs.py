"""Self organization navigation; mutable cookie selection requires CSRF."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE, set_active_org_cookie
from app.authz.policies import SELF, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_identity_db, verify_csrf
from app.schemas.organization import (
    ActiveOrganizationRequest,
    ActiveOrganizationResponse,
    OrganizationSummary,
)
from app.services import org_service

router = APIRouter(prefix="/api/me", tags=["organizations"])


@router.get("/organizations", response_model=list[OrganizationSummary])
@authz_policy(SELF)
async def list_organizations(
    request: Request,
    db: AsyncSession = Depends(get_identity_db),  # I-06: own memberships, every company
    user: CurrentUser = Depends(get_current_user),
    authz: AuthzService = Depends(get_authz),
) -> list[OrganizationSummary]:
    summaries = await org_service.list_user_organizations(
        db, user, active_cookie=request.cookies.get(ACTIVE_ORG_COOKIE)
    )
    enforced = authz.enforces("organization")
    return [s.model_copy(update={"capabilities_enforced": enforced}) for s in summaries]


@router.post(
    "/active-org", response_model=ActiveOrganizationResponse, dependencies=[Depends(verify_csrf)]
)
@authz_policy(SELF)
async def set_active_organization(
    payload: ActiveOrganizationRequest,
    response: Response,
    db: AsyncSession = Depends(get_identity_db),  # I-06: own memberships, every company
    user: CurrentUser = Depends(get_current_user),
) -> ActiveOrganizationResponse:
    context = await org_service.select_active_organization(db, user, payload.org_id)
    set_active_org_cookie(response, context.org_id)
    return ActiveOrganizationResponse.model_validate(context)
