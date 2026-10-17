"""``GET /api/resources/{type}/permissions?ids=…`` — per-row ``my_permissions`` (H-05).

Registered once per type (tool, mcp_server, credential, skill) with that type's
``CANDIDATES(type, can_use)`` policy: it is a list-page confirmation, so hidden,
foreign and missing ids are simply absent from the answer (plan 8.10, 13.9).
"""

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import CANDIDATES, authz_policy
from app.authz.resource_permissions import MAX_IDS, PERMISSIONS, VISIBLE, resource_permissions
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import get_db, get_org_context

router = APIRouter(tags=["sharing"])
Endpoint = Callable[..., Coroutine[Any, Any, Any]]


class ResourcePermissionItem(BaseModel):
    id: UUID
    my_permissions: list[str]


class ResourcePermissionList(BaseModel):
    items: list[ResourcePermissionItem]


def _register(kind: str) -> None:
    @authz_policy(CANDIDATES(kind, VISIBLE))
    async def list_permissions(
        ids: list[UUID] = Query(default_factory=list, max_length=MAX_IDS),
        db: AsyncSession = Depends(get_db),
        context: OrgContext = Depends(get_org_context),
        authz: AuthzService = Depends(get_authz),
    ) -> ResourcePermissionList:
        answered = await resource_permissions(db, authz, context, kind, ids)
        return ResourcePermissionList(
            items=[ResourcePermissionItem(id=i, my_permissions=perms) for i, perms in answered]
        )

    name = f"list_{kind}_permissions"
    list_permissions.__name__ = list_permissions.__qualname__ = name
    router.add_api_route(
        f"/api/resources/{kind}/permissions",
        list_permissions,
        methods=["GET"],
        response_model=ResourcePermissionList,
        name=name,
    )


for _kind in PERMISSIONS:
    _register(_kind)
