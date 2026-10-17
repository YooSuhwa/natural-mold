"""``GET|PUT /api/orgs/{org_id}/retention`` HTTP boundary (plan 9.7, S08; E-08)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.policies import GOVERNANCE, SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.orgs import mutation_request, org_read_context
from app.schemas.retention import OrgRetentionUpdate, OrgRetentionView, OrgRetentionWrite
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.retention import org_retention_view, update_org_retention
from app.services.orgs.versioning import etag

router = APIRouter(prefix="/api/orgs/{org_id}/retention", tags=["organization administration"])


async def retention_policy_context(
    org_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgControlContext:
    """Non-members get 404; members without ``can_manage_policy`` get 403."""
    return await load_org_control(db, user, OrgControlTarget(org_id, "can_manage_policy"))


@router.get("", response_model=OrgRetentionView)
@authz_policy(SELF)
async def get_org_retention(
    response: Response, context: OrgControlContext = Depends(org_read_context)
) -> OrgRetentionView:
    result = org_retention_view(context.organization)
    response.headers["ETag"] = etag(result.updated_at)
    return result


@router.put("", response_model=OrgRetentionWrite)
@authz_policy(GOVERNANCE)
async def put_org_retention(
    payload: OrgRetentionUpdate,
    _csrf: None = Depends(verify_csrf),
    context: OrgControlContext = Depends(retention_policy_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> JSONResponse:
    result = await update_org_retention(mutation, context, payload)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"ETag": etag(result.policy.updated_at)}
    )
