"""Organization administrators manage external research-search destinations and limits."""

from fastapi import APIRouter, Depends, Response

from app.authz.org_control import OrgControlContext
from app.authz.policies import GOVERNANCE, SELF, authz_policy
from app.dependencies import verify_csrf
from app.routers.org_catalog import org_policy_read_context
from app.routers.orgs import mutation_request
from app.schemas.research_search import ResearchSearchPolicy, ResearchSearchPolicyView
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.research_search import (
    research_search_policy_view,
    update_research_search_policy,
)
from app.services.orgs.versioning import etag

router = APIRouter(
    prefix="/api/orgs/{org_id}/research-search-policy", tags=["organization administration"]
)


@router.get("", response_model=ResearchSearchPolicyView)
@authz_policy(SELF)
async def get_research_search_policy(
    response: Response, context: OrgControlContext = Depends(org_policy_read_context)
) -> ResearchSearchPolicyView:
    view = research_search_policy_view(context.organization)
    response.headers["ETag"] = etag(view.updated_at)
    return view


@router.put("", response_model=ResearchSearchPolicyView, dependencies=[Depends(verify_csrf)])
@authz_policy(GOVERNANCE)
async def put_research_search_policy(
    payload: ResearchSearchPolicy,
    response: Response,
    context: OrgControlContext = Depends(org_policy_read_context),
    mutation: OrgMutationRequest = Depends(mutation_request),
) -> ResearchSearchPolicyView:
    view = await update_research_search_policy(mutation, context, payload)
    response.headers["ETag"] = etag(view.updated_at)
    return view
