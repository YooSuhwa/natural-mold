"""Company publication approval queue HTTP boundary (plan 9.7, 10.2, S05; E-09).

``can_manage_tenant_catalog`` (company administrator) for the list and both decisions;
the decision routes take the company lock and re-read the permission before deciding.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import GOVERNANCE, authz_policy
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.tenant_policies import company_context
from app.schemas.tenant_publication import (
    PublicationRejection,
    PublicationRequestPage,
    TenantPublicationState,
)
from app.services.tenants.pagination import TenantPageQuery
from app.services.tenants.publication_requests import (
    PublicationDecision,
    decide_publication_request,
    list_publication_requests,
)
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/publication-requests", tags=["company administration"]
)


@router.get("", response_model=PublicationRequestPage)
@authz_policy(GOVERNANCE)
async def get_publication_requests(
    query: Annotated[TenantPageQuery, Query()],
    context: TenantAdminContext = Depends(company_context),
    db: AsyncSession = Depends(get_db),
) -> PublicationRequestPage:
    return await list_publication_requests(db, context, query)


async def _decide(
    decision: PublicationDecision,
    context: TenantAdminContext,
    item_id: UUID,
    authz: AuthzService,
    note: str | None,
) -> TenantPublicationState:
    state, change = await decide_publication_request(
        decision, context, item_id, approve=note is None, note=note
    )
    await decision.db.commit()
    await apply_lifecycle(change, authz)
    return state


@router.post("/{item_id}/approve", response_model=TenantPublicationState)
@authz_policy(GOVERNANCE)
async def approve_publication_request(
    item_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantPublicationState:
    decision = PublicationDecision(db, request, user, authz.mode)
    return await _decide(decision, context, item_id, authz, None)


@router.post("/{item_id}/reject", response_model=TenantPublicationState)
@authz_policy(GOVERNANCE)
async def reject_publication_request(
    item_id: UUID,
    payload: PublicationRejection,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(company_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantPublicationState:
    decision = PublicationDecision(db, request, user, authz.mode)
    return await _decide(decision, context, item_id, authz, payload.note)
