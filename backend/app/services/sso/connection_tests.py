"""``POST /sso-connections/{id}/test`` (S30 "시험 로그인").

OIDC: returns the IdP authorization URL of a ``mode=test`` transaction; the callback
records the outcome and never signs anyone in. LDAP: checks the typed directory
account right away and returns the received attributes. A pass marks a draft
connection ``tested`` (an active one keeps its status); failures leave it as is.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.errors import sso_error
from app.auth.sso.ldap_login import ldap_profile
from app.auth.sso.oidc import authorization_url, discover
from app.auth.sso.profile import SsoProfile
from app.auth.sso.state import LoginTransaction, new_transaction
from app.authz import audit_actions
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.organization import Tenant
from app.models.organization_identity import SsoConnection
from app.models.user import User
from app.schemas.sso_connection import SsoTestRequest, SsoTestResponse, SsoTestResult
from app.services.sso.common import SsoMutation, audit, locked_admin
from app.services.sso.connection_view import connection_view, owned_connection
from app.services.sso.login import oidc_client
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)


async def _record(
    mutation: SsoMutation, tenant: Tenant, row: SsoConnection, result: SsoTestResult
) -> None:
    if result.ok:
        row.tested_at = datetime.now(UTC)
        if row.status in ("draft", "tested"):
            row.status = "tested"
    await audit(
        mutation,
        tenant,
        audit_actions.TENANT_SSO_CONNECTION_TEST,
        ("sso_connection", row.id, row.issuer),
        {"protocol": row.protocol, "ok": result.ok, "error": result.error},
    )
    await mutation.db.flush()


async def start_test(
    mutation: SsoMutation,
    context: TenantAdminContext,
    connection_id: UUID,
    payload: SsoTestRequest,
) -> tuple[SsoTestResponse, LoginTransaction | None]:
    db = mutation.db
    tenant = await locked_admin(mutation, context)
    row = await owned_connection(db, tenant.id, connection_id, lock=True)
    if row.status == "disabled":
        raise sso_error("sso_unavailable")
    if row.protocol == "oidc":
        transaction = new_transaction(row.id, "test", mutation.actor.id)
        try:
            provider = await discover(row.issuer)
            client = await oidc_client(db, mutation.request, row)
        except AppError as error:
            result = SsoTestResult(ok=False, error=error.code)
            await _record(mutation, tenant, row, result)
            response = SsoTestResponse(
                result=result, connection=connection_view(mutation.request, row)
            )
            await db.commit()
            return response, None
        url = authorization_url(provider, client, list(row.scopes or []), transaction)
        response = SsoTestResponse(
            authorize_url=url, connection=connection_view(mutation.request, row)
        )
        await db.commit()
        return response, transaction
    if not payload.username or payload.password is None:
        raise sso_error("sso_config_invalid")
    try:
        profile = await ldap_profile(db, row, payload.username, payload.password.get_secret_value())
        result = SsoTestResult(ok=True, claims=profile.summary())
    except AppError as error:
        result = SsoTestResult(ok=False, error=error.code)
    await _record(mutation, tenant, row, result)
    await db.refresh(row)
    response = SsoTestResponse(result=result, connection=connection_view(mutation.request, row))
    await db.commit()
    return response, None


async def finish_oidc_test(
    db: AsyncSession,
    request: Request,
    transaction: LoginTransaction,
    outcome: SsoProfile | AppError,
) -> str:
    """Record a test callback for the administrator who started it; returns the result."""
    admin = await db.get(User, transaction.admin_id) if transaction.admin_id else None
    row = await db.get(SsoConnection, transaction.connection_id)
    if admin is None or row is None:
        return "sso_state_invalid"
    actor = CurrentUser(
        id=admin.id, email=admin.email, name=admin.name, is_super_user=admin.is_super_user
    )
    result = (
        SsoTestResult(ok=False, error=outcome.code)
        if isinstance(outcome, AppError)
        else SsoTestResult(ok=True, claims=outcome.summary())
    )
    mutation = SsoMutation(db, request, actor)
    try:
        fresh = await load_tenant_admin_context(
            db, actor, TenantAdminTarget(row.tenant_id, permission="can_manage_tenant")
        )
        await _record(mutation, fresh.tenant, row, result)
    except AppError as error:
        await db.rollback()
        return error.code
    await db.commit()
    return "ok" if result.ok else str(result.error)
