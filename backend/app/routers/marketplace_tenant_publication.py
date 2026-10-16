"""``POST|DELETE /api/marketplace/items/{item_id}/tenant-publication`` (plan 9.7; E-09).

Both need the item's ``can_manage`` (the D-05 item gate; legacy keeps the owner/ACL
``manage`` check); the request also needs ``cap_publish_marketplace`` (legacy allow),
checked only after the item is authorized. Withdrawal never needs the capability.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.marketplace.tenant_publication import (
    PublicationCall,
    managed_item,
    publication_state,
    request_company_publication,
    withdraw_company_publication,
)
from app.schemas.tenant_publication import TenantPublicationState

router = APIRouter(
    prefix="/api/marketplace/items/{item_id}/tenant-publication", tags=["marketplace"]
)
MANAGE = RES("marketplace_item", "can_manage", param="item_id", min_visible="can_view")


async def _finish(
    db: AsyncSession, authz: AuthzService, state: TenantPublicationState, change: LifecycleChange
) -> TenantPublicationState:
    await db.commit()
    await apply_lifecycle(change, authz)
    return state


@router.post("", response_model=TenantPublicationState)
@authz_policy(MANAGE)
async def request_tenant_publication(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantPublicationState:
    item = await managed_item(db, authz, context, user, item_id, publish=True)
    call = PublicationCall(db, request, user, authz.mode)
    change = await request_company_publication(call, item)
    return await _finish(db, authz, publication_state(item), change)


@router.delete("", response_model=TenantPublicationState)
@authz_policy(MANAGE)
async def withdraw_tenant_publication(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> TenantPublicationState:
    item = await managed_item(db, authz, context, user, item_id, publish=False)
    call = PublicationCall(db, request, user, authz.mode)
    change = await withdraw_company_publication(call, item)
    return await _finish(db, authz, publication_state(item), change)
