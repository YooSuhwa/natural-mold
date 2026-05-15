"""/api/auth endpoints — register, login, logout, refresh, me.

ADR-016 §5. The router itself stays thin: parse → service call →
attach cookies → return body. Rate limits come from the shared
``app.state.limiter`` (slowapi).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import clear_auth_cookies, set_auth_cookies
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.exceptions import AppError
from app.rate_limit import limiter
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutResponse,
    RefreshResponse,
    RegisterRequest,
    UserResponse,
)
from app.services import auth_service, user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/register", response_model=AuthResponse, status_code=201)
@limiter.limit("5/hour")
async def register_endpoint(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Create an account + auto-login (cookies + body CSRF).

    First user signed up becomes ``is_super_user=true`` when
    ``allow_first_user_as_admin`` is enabled. See ADR-016 §8.4 for
    operational guidance on disabling this in production.
    """

    user = await auth_service.register(db, payload, request)
    access, refresh, csrf = await auth_service.issue_tokens(db, user, request)
    await user_service.record_login_success(db, user, ip=auth_service.client_ip(request))
    await db.commit()
    set_auth_cookies(
        response,
        access_token=access,
        refresh_token=refresh,
        csrf_token=csrf,
    )
    return AuthResponse(user=UserResponse.model_validate(user), csrf_token=csrf)


@router.post("/login", response_model=AuthResponse)
@limiter.limit("10/minute")
async def login_endpoint(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Verify credentials, mint cookies, return user + CSRF body."""

    user = await auth_service.authenticate(
        db, email=payload.email, password=payload.password
    )
    access, refresh, csrf = await auth_service.issue_tokens(db, user, request)
    await user_service.record_login_success(db, user, ip=auth_service.client_ip(request))
    await db.commit()
    set_auth_cookies(
        response,
        access_token=access,
        refresh_token=refresh,
        csrf_token=csrf,
    )
    return AuthResponse(user=UserResponse.model_validate(user), csrf_token=csrf)


@router.post("/logout", response_model=LogoutResponse)
async def logout_endpoint(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> LogoutResponse:
    """Revoke the active refresh row + clear cookies.

    CSRF-checked so a malicious site can't log the user out via a
    cross-origin POST.
    """

    refresh = request.cookies.get(settings.cookie_name_refresh)
    if refresh:
        await auth_service.revoke_refresh(db, refresh)
        await db.commit()
    clear_auth_cookies(response)
    return LogoutResponse(ok=True)


@router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("30/minute")
async def refresh_endpoint(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    """Rotate the refresh cookie. No CSRF — cookie alone authorizes."""

    refresh = request.cookies.get(settings.cookie_name_refresh)
    if not refresh:
        raise AppError(
            code="invalid_refresh", message="세션이 만료되었습니다", status=401
        )
    access, new_refresh, csrf, _user = await auth_service.rotate_refresh(
        db, refresh, request
    )
    await db.commit()
    set_auth_cookies(
        response,
        access_token=access,
        refresh_token=new_refresh,
        csrf_token=csrf,
    )
    return RefreshResponse(csrf_token=csrf)


@router.get("/me", response_model=UserResponse)
async def me_endpoint(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> UserResponse:
    """Return the authenticated user.

    Re-fetches the row so fields like ``last_login_at`` (not carried
    on ``CurrentUser``) reflect persisted state.
    """

    db_user = await user_service.get_by_id(db, user.id)
    if db_user is None:
        # Should be impossible — ``get_current_user`` already loaded the row.
        raise AppError(
            code="not_authenticated", message="인증이 필요합니다", status=401
        )
    return UserResponse.model_validate(db_user)


__all__ = ["router"]
