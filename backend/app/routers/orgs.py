"""Organization metadata and governance mutations."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.policies import SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.schemas.org import OrganizationPatch, OrganizationView
from app.services.orgs.info import OrgMutationRequest, organization_view, update_organization_info
from app.services.orgs.versioning import etag

router = APIRouter(prefix="/api/orgs", tags=["organization administration"])


async def org_read_context(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgControlContext:
    return await load_org_control(db, user, OrgControlTarget(org_id))


async def org_policy_context(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgControlContext:
    return await load_org_control(db, user, OrgControlTarget(org_id, "can_manage_policy"))


async def mutation_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> OrgMutationRequest:
    return OrgMutationRequest(db=db, request=request, expected_version=if_match)


async def org_members_context(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgControlContext:
    return await load_org_control(
        db,
        user,
        OrgControlTarget(org_id, "can_manage_members", lock_organization=False),
    )


@router.get("/{org_id}", response_model=OrganizationView)
@authz_policy(SELF)
async def get_organization(
    response: Response,
    context: OrgControlContext = Depends(org_read_context),
) -> OrganizationView:
    result = organization_view(context)
    response.headers["ETag"] = etag(result.updated_at)
    return result


@router.patch("/{org_id}", response_model=OrganizationView, dependencies=[Depends(verify_csrf)])
@authz_policy(SELF)
async def patch_organization(
    payload: OrganizationPatch,
    context: OrgControlContext = Depends(org_policy_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await update_organization_info(mutation, context, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.updated_at)}
    )
