"""Share, access-list and ownership-transfer API (plan 9.7 "공유" rows, 10.2, 10.7; E-01).

``/api/resources/{type}/{id}/…`` is registered once per shareable type so every route
declares its exact ``RES`` policy (type, permission, 8.4 ``min_visible``):

* ``GET  …/grants``, ``GET …/access``: ``can_revoke`` (= ``can_share`` or governance)
* ``POST …/grants``: ``can_share`` (+ ``cap_share_org_wide`` for the whole organization)
* ``POST …/transfer``: ``can_govern`` (owner or organization/company admin)
* ``DELETE /api/grants/{grant_id}``: ``can_revoke`` on the grant's resource; the grant id
  of a resource the caller cannot revoke on is 404 (no enumeration).

Mutations commit, then apply the queued tuples before responding (8.6 item 2).
"""

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.delegation_sweep import revalidate_grant, revalidate_object
from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.authz.share_access import access_list
from app.authz.share_targets import SHARE_TYPES, ShareType
from app.authz.share_transfer import transfer
from app.authz.sharing import grant_view, list_shares, revoke_share, share
from app.dependencies import get_db, get_org_context, verify_csrf
from app.schemas.grant import (
    AccessList,
    GrantCreate,
    GrantList,
    GrantView,
    OwnershipTransfer,
    OwnershipTransferResult,
)

router = APIRouter(tags=["sharing"])
Endpoint = Callable[..., Coroutine[Any, Any, Any]]


def _named(endpoint: Endpoint, name: str) -> Endpoint:
    endpoint.__name__ = endpoint.__qualname__ = name
    return endpoint


def _register(kind: ShareType) -> None:
    base = f"/api/resources/{kind.name}/{{resource_id}}"

    def policy(permission: str) -> Callable[[Endpoint], Endpoint]:
        return authz_policy(
            RES(kind.name, permission, param="resource_id", min_visible=kind.min_visible)
        )

    @policy("can_revoke")
    async def list_grants(
        resource_id: UUID,
        db: AsyncSession = Depends(get_db),
        context: OrgContext = Depends(get_org_context),
        authz: AuthzService = Depends(get_authz),
    ) -> GrantList:
        return GrantList(items=await list_shares(db, authz, context, kind, resource_id))

    @policy("can_share")
    async def create_grant(
        resource_id: UUID,
        body: GrantCreate,
        response: Response,
        db: AsyncSession = Depends(get_db),
        _csrf: None = Depends(verify_csrf),
        context: OrgContext = Depends(get_org_context),
        authz: AuthzService = Depends(get_authz),
    ) -> GrantView:
        result = await share(db, authz, context, kind, resource_id, body)
        view = grant_view(result.grant)
        await db.commit()
        await apply_lifecycle(result.change, authz)
        await revalidate_object(  # a stage change may narrow access (10.2, 10.6)
            authz,
            context.org_id,
            context.tenant_id,
            (kind.name, resource_id),
            trigger="grant_update",
        )
        response.status_code = 201 if result.created else 200
        return view

    @policy("can_revoke")
    async def get_access(
        resource_id: UUID,
        db: AsyncSession = Depends(get_db),
        context: OrgContext = Depends(get_org_context),
        authz: AuthzService = Depends(get_authz),
    ) -> AccessList:
        return await access_list(db, authz, context, kind, resource_id)

    @policy("can_govern")
    async def transfer_ownership(
        resource_id: UUID,
        body: OwnershipTransfer,
        db: AsyncSession = Depends(get_db),
        _csrf: None = Depends(verify_csrf),
        context: OrgContext = Depends(get_org_context),
        authz: AuthzService = Depends(get_authz),
    ) -> OwnershipTransferResult:
        result = await transfer(db, authz, context, kind, resource_id, body)
        await db.commit()
        await apply_lifecycle(result.change, authz)
        await revalidate_object(  # 10.7: delegations stay and are re-checked
            authz,
            context.org_id,
            context.tenant_id,
            (kind.name, resource_id),
            trigger="ownership_transfer",
        )
        return result.body

    routes: list[tuple[str, str, Endpoint, type, int]] = [
        ("GET", "/grants", list_grants, GrantList, 200),
        ("POST", "/grants", create_grant, GrantView, 201),
        ("GET", "/access", get_access, AccessList, 200),
        ("POST", "/transfer", transfer_ownership, OwnershipTransferResult, 200),
    ]
    for method, suffix, endpoint, model, status in routes:
        router.add_api_route(
            base + suffix,
            _named(endpoint, f"{endpoint.__name__}_{kind.name}"),
            methods=[method],
            response_model=model,
            status_code=status,
        )


for _kind in SHARE_TYPES.values():
    _register(_kind)


@router.delete("/api/grants/{grant_id}", status_code=204)
@authz_policy(RES("{resource_type}", "can_revoke", param="grant_id", min_visible="can_revoke"))
async def delete_grant(
    grant_id: UUID,
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> Response:
    change = await revoke_share(db, authz, context, grant_id)
    await db.commit()
    await apply_lifecycle(change, authz)
    await revalidate_grant(authz, context.org_id, context.tenant_id, grant_id)  # 10.2 → 10.6
    return Response(status_code=204)
