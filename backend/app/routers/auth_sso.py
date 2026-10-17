"""Email-first SSO login (plan 9.7, 10.10; H-08). All routes are PUBLIC.

* ``POST /api/auth/sso/discover`` — email → the company's active connections (verified
  domains only) or ``password``; also issues the login CSRF token for LDAP.
* ``GET /api/auth/sso/{id}/start`` — OIDC Authorization Code + PKCE redirect.
* ``GET /auth/callback/{id}`` — state-checked callback (instead of CSRF); signs in and
  redirects to the app, or records a test login for the administrator.
* ``POST /api/auth/ldap/{id}/login`` — login CSRF + rate limits; failures are uniform.
"""

import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso import state
from app.auth.sso.errors import sso_error
from app.auth.sso.ldap_login import ldap_profile
from app.auth.sso.link_pending import set_link_cookie
from app.auth.sso.oidc import authorization_url, discover
from app.auth.sso.provisioning import LinkPending, sso_sign_in
from app.authz.policies import PUBLIC, authz_policy
from app.config import settings
from app.dependencies import get_db
from app.exceptions import AppError
from app.rate_limit import limiter
from app.schemas.auth import AuthResponse, UserResponse
from app.schemas.sso import LdapLoginRequest, SsoDiscoverRequest, SsoDiscoverResponse
from app.services.sso import connection_tests
from app.services.sso.discovery import discover_login
from app.services.sso.login import (
    ConnectionRef,
    connection_ref,
    finish_login,
    login_connection,
    oidc_callback_profile,
    oidc_client,
    record_failure,
)

router = APIRouter(tags=["auth sso"])
_LOGIN_CSRF_HEADER = "X-Login-CSRF"


@router.post("/api/auth/sso/discover", response_model=SsoDiscoverResponse)
@authz_policy(PUBLIC)
@limiter.limit("30/minute")
async def sso_discover(
    request: Request,
    response: Response,
    payload: SsoDiscoverRequest,
    db: AsyncSession = Depends(get_db),
) -> SsoDiscoverResponse:
    login_csrf = secrets.token_urlsafe(32)
    response.set_cookie(
        state.LOGIN_CSRF_COOKIE,
        login_csrf,
        max_age=600,
        path="/api/auth/ldap",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return await discover_login(db, request, payload.email, login_csrf)


@router.get("/api/auth/sso/{connection_id}/start", status_code=302, name="sso_start")
@authz_policy(PUBLIC)
@limiter.limit("30/minute")
async def sso_start(
    request: Request, connection_id: UUID, db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    try:
        row = await login_connection(db, connection_id, "oidc")
        provider = await discover(row.issuer)
        client = await oidc_client(db, request, row)
    except AppError as error:
        return RedirectResponse(state.frontend_url(f"/login?sso_error={error.code}"), 302)
    transaction = state.new_transaction(row.id)
    target = authorization_url(provider, client, list(row.scopes or []), transaction)
    redirect = RedirectResponse(target, 302)
    state.set_transaction_cookie(redirect, transaction)
    return redirect


@router.get("/auth/callback/{connection_id}", status_code=302, name="sso_callback")
@authz_policy(PUBLIC)
@limiter.limit("60/minute")
async def sso_callback(
    request: Request,
    connection_id: UUID,
    code: str | None = Query(default=None, max_length=4096),
    state_param: str | None = Query(default=None, alias="state", max_length=512),
    error: str | None = Query(default=None, max_length=256),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    tx = state.read_transaction(request.cookies.get(state.STATE_COOKIE), state_param)
    if tx is None or tx.connection_id != connection_id:
        return _redirect("/login?sso_error=sso_state_invalid")
    ref: ConnectionRef | None = None
    try:
        row = await login_connection(db, connection_id, "oidc", testing=tx.mode == "test")
        ref = connection_ref(row)
        if error or not code:
            raise sso_error("sso_idp_error")
        profile = await oidc_callback_profile(db, request, row, (code, tx))
        if tx.mode == "test":
            result = await connection_tests.finish_oidc_test(db, request, tx, profile)
            return _redirect(f"/settings/company/sso?test={connection_id}&result={result}")
        try:
            user = await sso_sign_in(db, row, profile, request)
        except LinkPending as pending:
            await record_failure(db, request, ref, "sso_link_pending")
            redirect = _redirect("/login?sso_link=pending")
            set_link_cookie(redirect, pending.user_id, connection_id, profile)
            return redirect
        redirect = _redirect("/")
        await finish_login(db, request, redirect, user, row)
        return redirect
    except AppError as failure:
        if tx.mode == "test":
            await db.rollback()
            result = await connection_tests.finish_oidc_test(db, request, tx, failure)
            return _redirect(f"/settings/company/sso?test={connection_id}&result={result}")
        await record_failure(db, request, ref, failure.code)
        return _redirect(f"/login?sso_error={failure.code}")


def _redirect(path: str) -> RedirectResponse:
    redirect = RedirectResponse(state.frontend_url(path), 302)
    state.clear_transaction_cookie(redirect)
    return redirect


@router.post("/api/auth/ldap/{connection_id}/login", response_model=AuthResponse)
@authz_policy(PUBLIC)
@limiter.limit("20/minute")
async def ldap_login(
    request: Request,
    response: Response,
    connection_id: UUID,
    payload: LdapLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse | JSONResponse:
    header = request.headers.get(_LOGIN_CSRF_HEADER) or ""
    cookie = request.cookies.get(state.LOGIN_CSRF_COOKIE) or ""
    if not header or not cookie or not secrets.compare_digest(header, cookie):
        raise sso_error("login_csrf_invalid")
    ref: ConnectionRef | None = None
    try:
        row = await login_connection(db, connection_id, "ldap")
        ref = connection_ref(row)
        profile = await ldap_profile(db, row, payload.username, payload.password.get_secret_value())
        user = await sso_sign_in(db, row, profile, request)
    except LinkPending as pending:
        await record_failure(db, request, ref, "sso_link_pending")
        error = sso_error("sso_link_pending")
        pending_response = JSONResponse(
            status_code=error.status,
            content={"error": {"code": error.code, "message": error.message, "details": None}},
        )
        set_link_cookie(pending_response, pending.user_id, connection_id, profile)
        return pending_response
    except AppError as failure:
        await record_failure(db, request, ref, failure.code)
        if failure.code in ("login_rate_limited", "ldap_unavailable"):
            raise
        raise sso_error("invalid_credentials") from None  # 10.10: never say why
    csrf = await finish_login(db, request, response, user, row)
    return AuthResponse(user=UserResponse.model_validate(user), csrf_token=csrf)
