"""Principal lookup is constrained to the selected org and throttled per (company, user)."""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.policies import SELF, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context
from app.rate_limit import caller_key, limiter
from app.schemas.org_search import PrincipalSearchQuery, PrincipalSearchView
from app.services.orgs.search import search_principals

router = APIRouter(prefix="/api/orgs", tags=["organization principal search"])


@dataclass(frozen=True, slots=True)
class PrincipalSearchContext:
    db: AsyncSession
    control: OrgControlContext


async def current_target_org(
    org_id: UUID, current: OrgContext = Depends(get_org_context)
) -> OrgContext:
    if org_id != current.org_id:
        raise authorization_error("org_access_denied")
    return current


async def search_context(
    user: CurrentUser = Depends(get_current_user),
    current: OrgContext = Depends(current_target_org),
    db: AsyncSession = Depends(get_db),
) -> PrincipalSearchContext:
    return PrincipalSearchContext(
        db=db, control=await load_org_control(db, user, OrgControlTarget(current.org_id))
    )


principal_rate_key = caller_key("principal-search")  # per (company, user), I-08


@router.get("/{org_id}/principals", response_model=list[PrincipalSearchView])
@authz_policy(SELF)
@limiter.limit("30/minute", key_func=principal_rate_key)
async def find_principals(
    request: Request,
    query: Annotated[PrincipalSearchQuery, Query()],
    context: PrincipalSearchContext = Depends(search_context),
) -> list[PrincipalSearchView]:
    return await search_principals(context.db, context.control, query)
