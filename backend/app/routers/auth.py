"""/api/auth endpoints — register, login, logout, refresh, me.

ADR-016 §5. The router itself stays thin: parse → service call →
attach cookies → return body. Rate limits come from the shared
``app.state.limiter`` (slowapi).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import FileResponse
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
    ProfileUpdateRequest,
    RefreshResponse,
    RegisterRequest,
    UserResponse,
)
from app.services import audit_service, auth_service, user_profile_service, user_service

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
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="auth.register",
        target_type="user",
        target_id=user.id,
        target_name_snapshot=user.email,
        outcome="success",
        request=request,
        metadata={
            "auto_login": True,
            "promoted_to_super_user": bool(user.is_super_user),
        },
    )
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        target_name_snapshot=user.email,
        outcome="success",
        request=request,
        metadata={"source": "register"},
    )
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

    try:
        user = await auth_service.authenticate(
            db, email=payload.email, password=payload.password
        )
    except AppError as exc:
        await audit_service.record_event(
            db,
            actor_type="user",
            actor_email_snapshot=payload.email,
            action="auth.login",
            target_type="user",
            outcome="failure",
            reason_code=exc.code,
            reason_message=exc.message,
            request=request,
            metadata={"email": payload.email},
        )
        await db.commit()
        raise
    access, refresh, csrf = await auth_service.issue_tokens(db, user, request)
    await user_service.record_login_success(db, user, ip=auth_service.client_ip(request))
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        target_name_snapshot=user.email,
        outcome="success",
        request=request,
    )
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
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=_user.id,
        actor_email_snapshot=_user.email,
        owner_user_id=_user.id,
        owner_email_snapshot=_user.email,
        action="auth.logout",
        target_type="user",
        target_id=_user.id,
        target_name_snapshot=_user.email,
        outcome="success",
        request=request,
    )
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
    access, new_refresh, csrf, user = await auth_service.rotate_refresh(
        db, refresh, request
    )
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="auth.refresh",
        target_type="user",
        target_id=user.id,
        target_name_snapshot=user.email,
        outcome="success",
        request=request,
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


async def _load_profile_user(db: AsyncSession, user: CurrentUser):
    db_user = await user_service.get_by_id(db, user.id)
    if db_user is None:
        raise AppError(
            code="not_authenticated", message="인증이 필요합니다", status=401
        )
    return db_user


@router.patch("/me/profile", response_model=UserResponse)
async def update_profile_endpoint(
    request: Request,
    payload: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> UserResponse:
    db_user = await _load_profile_user(db, user)
    changed_fields = sorted(payload.model_fields_set)
    user_profile_service.apply_profile_update(db_user, payload)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="auth.profile_update",
        target_type="user",
        target_id=user.id,
        target_name_snapshot=user.email,
        outcome="success",
        request=request,
        metadata={"changed_fields": changed_fields},
    )
    await db.commit()
    await db.refresh(db_user)
    return UserResponse.model_validate(db_user)


@router.post("/me/avatar-image", response_model=UserResponse)
async def upload_avatar_image_endpoint(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> UserResponse:
    db_user = await _load_profile_user(db, user)
    await user_profile_service.save_avatar_image(db_user, file)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="auth.avatar_upload",
        target_type="user",
        target_id=user.id,
        target_name_snapshot=user.email,
        outcome="success",
        request=request,
        metadata={"filename": file.filename, "content_type": file.content_type},
    )
    await db.commit()
    await db.refresh(db_user)
    return UserResponse.model_validate(db_user)


@router.delete("/me/avatar-image", response_model=UserResponse)
async def delete_avatar_image_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> UserResponse:
    db_user = await _load_profile_user(db, user)
    await user_profile_service.delete_avatar_image(db_user)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="auth.avatar_delete",
        target_type="user",
        target_id=user.id,
        target_name_snapshot=user.email,
        outcome="success",
        request=request,
    )
    await db.commit()
    await db.refresh(db_user)
    return UserResponse.model_validate(db_user)


@router.get("/me/avatar-image")
async def get_avatar_image_endpoint(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    db_user = await _load_profile_user(db, user)
    path = await user_profile_service.avatar_image_file(db_user)
    if path is None:
        await db.commit()
        return Response(status_code=204)
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=300"},
    )


__all__ = ["router"]
