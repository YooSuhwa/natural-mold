"""FastAPI dependencies — DB session, current user (JWT), CSRF guard.

ADR-016 §6.1. ``get_current_user`` retains its signature (returns a
``CurrentUser``) so existing routers keep compiling, but the body is
swapped from a hard-coded mock user to JWT cookie/Authorization-header
extraction. Tests inject auth via the cookies emitted by
``/api/auth/login`` (or directly call ``app.auth.jwt.create_access_token``).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import InvalidTokenError, decode_token
from app.config import settings
from app.database import async_session
from app.exceptions import AppError
from app.services import user_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CurrentUser:
    """Lightweight projection of ``User`` carried through the request.

    Frozen + minimal: routers must not mutate the User row through this
    proxy. Workspace expansion (ADR-016 §7) will add an optional
    ``workspace_id`` here without breaking call sites.
    """

    id: uuid.UUID
    email: str
    name: str
    display_name: str | None = None
    avatar_mode: str = "auto"
    avatar_initials: str | None = None
    avatar_color: str = "mint"
    avatar_image_url: str | None = None
    is_super_user: bool = False


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _extract_access_token(request: Request) -> str | None:
    """Pull the access token from the cookie first, Bearer header second.

    Cookie wins so the SPA's HttpOnly flow is the canonical path. Bearer
    fallback exists for tooling (curl, integration tests) — the server
    never differentiates between sources.
    """

    cookie = request.cookies.get(settings.cookie_name_access)
    if cookie:
        return cookie
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


async def _resolve_user(token: str, db: AsyncSession) -> CurrentUser | None:
    try:
        payload = decode_token(token, expected_type="access")
    except InvalidTokenError:
        return None
    try:
        user_id = uuid.UUID(payload.sub)
    except (TypeError, ValueError):
        return None
    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        return None
    return CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        display_name=user.display_name,
        avatar_mode=user.avatar_mode,
        avatar_initials=user.avatar_initials,
        avatar_color=user.avatar_color,
        avatar_image_url=user.avatar_image_url,
        is_super_user=user.is_super_user,
    )


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Require a valid access token. Raises 401 ``not_authenticated``."""

    token = _extract_access_token(request)
    if not token:
        raise AppError(code="not_authenticated", message="인증이 필요합니다", status=401)
    user = await _resolve_user(token, db)
    if user is None:
        raise AppError(code="not_authenticated", message="인증이 필요합니다", status=401)
    request.state.current_user = user
    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CurrentUser | None:
    """Anonymous-friendly variant. Returns ``None`` instead of 401."""

    token = _extract_access_token(request)
    if not token:
        return None
    user = await _resolve_user(token, db)
    if user is not None:
        request.state.current_user = user
    return user


async def require_super_user(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Gate operator-only endpoints (system credentials, model catalog…)."""

    if not user.is_super_user:
        raise AppError(code="forbidden", message="권한이 없습니다", status=403)
    return user


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def verify_csrf(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> None:
    """Double-submit CSRF check (header == cookie, both signed for ``user``).

    ``GET``/``HEAD``/``OPTIONS`` are exempt (no state change). For
    mutations we require ``X-CSRF-Token`` to match the ``moldy_csrf``
    cookie *and* its JWT ``sub`` to equal the current user — so a
    forwarded cookie from a different account is rejected.
    """

    if request.method in _CSRF_SAFE_METHODS:
        return
    header = request.headers.get("x-csrf-token") or request.headers.get("X-CSRF-Token")
    cookie = request.cookies.get(settings.cookie_name_csrf)
    if not header or not cookie or not secrets.compare_digest(header, cookie):
        raise AppError(code="csrf_mismatch", message="CSRF 검증 실패", status=403)
    try:
        payload = decode_token(header, expected_type="csrf")
    except InvalidTokenError as exc:
        raise AppError(code="csrf_mismatch", message="CSRF 검증 실패", status=403) from exc
    if payload.sub != str(user.id):
        raise AppError(code="csrf_mismatch", message="CSRF 검증 실패", status=403)
