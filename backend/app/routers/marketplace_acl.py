"""``GET /api/marketplace/items/{item_id}/acl``: named ACL recipients (S16, E-04).

The marketplace share dialog lists a restricted item's recipients; the item payload
carries only their ids (``acl_user_ids``). Needs the item's ``can_manage`` — the same
D-05 gate (legacy: owner/ACL ``manage``) as the ACL replace and revoke routes.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context
from app.marketplace.acl_names import acl_recipients
from app.marketplace.tenant_publication import managed_item
from app.schemas.marketplace_acl import MarketplaceAclList

router = APIRouter(prefix="/api/marketplace/items/{item_id}/acl", tags=["marketplace"])
MANAGE = RES("marketplace_item", "can_manage", param="item_id", min_visible="can_view")


@router.get("", response_model=MarketplaceAclList)
@authz_policy(MANAGE)
async def list_item_acl(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> MarketplaceAclList:
    item = await managed_item(db, authz, context, user, item_id, publish=False)
    return await acl_recipients(db, item)
