"""S30 ``/api/tenants/{tenant_id}/sso-connections`` (plan 9.7; H-08). ``can_manage_tenant``.

CRUD, ``POST /{id}/test`` (OIDC: authorization URL + test transaction cookie; LDAP:
immediate directory check) and ``POST /{id}/activate`` (only after a passed test).
"""

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.state import set_transaction_cookie
from app.authz.policies import GOVERNANCE, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.tenant_sso import manager_context
from app.schemas.sso_connection import (
    SsoConnectionCreate,
    SsoConnectionList,
    SsoConnectionRead,
    SsoConnectionUpdate,
    SsoTestRequest,
    SsoTestResponse,
)
from app.services.sso import connection_tests, connections
from app.services.sso.common import SsoMutation
from app.services.sso.connection_view import connection_view, owned_connection
from app.tenancy.admin_context import TenantAdminContext

router = APIRouter(prefix="/api/tenants/{tenant_id}/sso-connections", tags=["company sso"])


@router.get("", response_model=SsoConnectionList)
@authz_policy(GOVERNANCE)
async def list_sso_connections(
    request: Request,
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(manager_context),
) -> SsoConnectionList:
    return await connections.list_connections(db, request, context.tenant.id)


@router.post("", response_model=SsoConnectionRead, status_code=201)
@authz_policy(GOVERNANCE)
async def create_sso_connection(
    request: Request,
    payload: SsoConnectionCreate = Body(...),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> SsoConnectionRead:
    return await connections.create_connection(SsoMutation(db, request, user), context, payload)


@router.get("/{connection_id}", response_model=SsoConnectionRead)
@authz_policy(GOVERNANCE)
async def get_sso_connection(
    connection_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    context: TenantAdminContext = Depends(manager_context),
) -> SsoConnectionRead:
    return connection_view(request, await owned_connection(db, context.tenant.id, connection_id))


@router.patch("/{connection_id}", response_model=SsoConnectionRead)
@authz_policy(GOVERNANCE)
async def update_sso_connection(
    connection_id: UUID,
    payload: SsoConnectionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> SsoConnectionRead:
    mutation = SsoMutation(db, request, user)
    return await connections.update_connection(mutation, context, connection_id, payload)


@router.delete("/{connection_id}", status_code=204)
@authz_policy(GOVERNANCE)
async def delete_sso_connection(
    connection_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> Response:
    await connections.delete_connection(SsoMutation(db, request, user), context, connection_id)
    return Response(status_code=204)


@router.post("/{connection_id}/test", response_model=SsoTestResponse)
@authz_policy(GOVERNANCE)
async def test_sso_connection(
    connection_id: UUID,
    request: Request,
    payload: SsoTestRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> JSONResponse:
    mutation = SsoMutation(db, request, user)
    result, transaction = await connection_tests.start_test(
        mutation, context, connection_id, payload or SsoTestRequest()
    )
    response = JSONResponse(content=result.model_dump(mode="json"))
    if transaction is not None:
        set_transaction_cookie(response, transaction)
    return response


@router.post("/{connection_id}/activate", response_model=SsoConnectionRead)
@authz_policy(GOVERNANCE)
async def activate_sso_connection(
    connection_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: TenantAdminContext = Depends(manager_context),
) -> SsoConnectionRead:
    return await connections.activate_connection(
        SsoMutation(db, request, user), context, connection_id
    )
