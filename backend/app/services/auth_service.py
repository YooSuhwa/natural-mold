"""Auth flows: register, authenticate, issue tokens, rotate refresh, revoke.

Stateless on purpose — every helper takes the DB session as the first
argument so the router can drive transaction boundaries. Refresh
rotation + replay detection are concentrated here so the policy lives in
one place.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import (
    InvalidTokenError,
    create_access_token,
    create_csrf_token,
    create_refresh_token,
    decode_token,
    hash_refresh_token,
)
from app.auth.password import hash_password, verify_password
from app.config import settings
from app.exceptions import AppError
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.services import user_service

logger = logging.getLogger(__name__)

# Pre-computed bcrypt hash used to keep ``authenticate()`` timing roughly
# constant when the email doesn't exist. Computed once at import time — running
# verify against a real (but unguessable) hash takes the same ~250ms as
# verifying against a real user's hash, blocking a timing oracle that would
# otherwise leak email existence. See ADR-016 §5.1.
_DUMMY_PASSWORD_HASH = hash_password("__timing_pad__")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def client_ip(request: Request) -> str | None:
    """Best-effort IP extraction (X-Forwarded-For first, then peer)."""

    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


async def register(
    db: AsyncSession, payload: RegisterRequest, request: Request
) -> User:
    """Create a new user + auto-promote the first signup to super_user."""

    if await user_service.email_exists(db, payload.email):
        raise AppError(
            code="email_already_exists",
            message="이미 가입된 이메일입니다",
            status=409,
        )
    promote = settings.allow_first_user_as_admin and await user_service.is_first_user(db)
    user = await user_service.create_user(
        db,
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name,
        is_super_user=promote,
    )
    if promote:
        logger.info("First user signup auto-promoted to super_user: %s", user.email)
    return user


# ---------------------------------------------------------------------------
# Authenticate (login)
# ---------------------------------------------------------------------------


async def authenticate(
    db: AsyncSession, *, email: str, password: str
) -> User:
    """Verify credentials and bump login bookkeeping.

    Raises ``AppError`` with the appropriate status:
    * 401 ``invalid_credentials`` — wrong email/password (uniform message
      to avoid an enumeration oracle).
    * 423 ``account_locked`` — too many recent failures.
    * 403 ``account_inactive`` — admin-disabled account.
    """

    user = await user_service.get_by_email(db, email)
    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AppError(
            code="invalid_credentials",
            message="이메일 또는 비밀번호가 올바르지 않습니다",
            status=401,
        )
    if not user.is_active:
        raise AppError(
            code="account_inactive",
            message="비활성화된 계정입니다",
            status=403,
        )
    if user_service.is_locked(user):
        raise AppError(
            code="account_locked",
            message="로그인 시도가 많아 계정이 잠겼습니다. 잠시 후 다시 시도하세요",
            status=423,
        )
    if not verify_password(password, user.hashed_password):
        await user_service.record_login_failure(db, user)
        # Persist the bumped counter BEFORE raising — FastAPI's
        # session-per-request dependency rolls the transaction back on
        # exception, which would silently revert the increment and
        # render the lockout policy inert. See ADR-016 §5.1.
        await db.commit()
        raise AppError(
            code="invalid_credentials",
            message="이메일 또는 비밀번호가 올바르지 않습니다",
            status=401,
        )
    return user


# ---------------------------------------------------------------------------
# Token issuance + refresh rotation
# ---------------------------------------------------------------------------


async def issue_tokens(
    db: AsyncSession, user: User, request: Request
) -> tuple[str, str, str]:
    """Mint access/refresh/csrf and persist the refresh hash.

    Returns ``(access, refresh, csrf)`` for the cookie helper.
    """

    access, _refresh_row, refresh, csrf = await _issue_tokens_with_row(
        db, user, request
    )
    return access, refresh, csrf


async def _issue_tokens_with_row(
    db: AsyncSession, user: User, request: Request
) -> tuple[str, RefreshToken, str, str]:
    """Like :func:`issue_tokens` but also returns the new ``RefreshToken``.

    Internal helper for the rotation flow, which needs the row id to
    wire ``old.replaced_by_id`` for the race-vs-replay disambiguation.
    """

    access = create_access_token(user.id, is_super_user=user.is_super_user)
    refresh, _jti, refresh_hash = create_refresh_token(user.id)
    csrf = create_csrf_token(user.id)

    expires_at = datetime.now(UTC) + timedelta(
        days=settings.refresh_token_expire_days
    )
    row = RefreshToken(
        user_id=user.id,
        token_hash=refresh_hash,
        expires_at=expires_at,
        user_agent=user_agent(request),
        ip=client_ip(request),
    )
    db.add(row)
    await db.flush()
    return access, row, refresh, csrf


async def _revoke_all_active(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Force-logout: revoke every active refresh row for the user."""

    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def _aware(dt: datetime) -> datetime:
    """Coerce a naive datetime (SQLite test rows) to UTC-aware."""

    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _is_race_not_replay(
    row: RefreshToken, request: Request, now: datetime
) -> bool:
    """Decide if a re-presented (revoked) refresh row is a tab-race.

    True ⇒ caller should chain-rotate from the replacement instead of
    treating the request as a replay attack. Requires ALL of:

    1. ``replaced_by_id`` is set (i.e. the row was rotated, not revoked
       via logout or mass-revoke).
    2. The revocation is within ``refresh_rotation_grace_seconds``.
    3. The originating user-agent matches the current request — a cheap
       binding that defeats a stolen-cookie attacker on a different
       browser. Not cryptographic, but materially raises the bar over
       "any presenter of the stale token wins".

    The replacement's liveness is verified by the caller after we fetch
    the row — keeps this predicate pure / synchronous.
    """

    if row.replaced_by_id is None or row.revoked_at is None:
        return False
    grace = timedelta(seconds=settings.refresh_rotation_grace_seconds)
    if grace.total_seconds() <= 0:
        return False
    if now - _aware(row.revoked_at) > grace:
        return False
    return (row.user_agent or "") == (user_agent(request) or "")


async def rotate_refresh(
    db: AsyncSession, refresh_token: str, request: Request
) -> tuple[str, str, str, User]:
    """Validate + rotate a refresh token.

    Three outcomes for a re-presented (already-revoked) row:

    * **Race** — replacement is still active, revocation is within the
      grace window, and the originating UA matches. Chain-rotates from
      the replacement so the losing tab also gets fresh cookies. No
      mass-revoke.
    * **Replay** — any other revoked-row presentation. Burns the user's
      entire refresh whitelist (force re-login) and 401s.
    * **Live** — normal path: revoke the row, mint replacement, link
      ``old.replaced_by_id``.

    Returns ``(access, refresh, csrf, user)`` so the router can set
    cookies and respond with the latest user projection.
    """

    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except InvalidTokenError as exc:
        raise AppError(
            code="invalid_refresh", message="세션이 만료되었습니다", status=401
        ) from exc

    digest = hash_refresh_token(refresh_token)
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if row is None:
        # Hash unknown — token forged or already GC'd.
        raise AppError(
            code="invalid_refresh", message="세션이 만료되었습니다", status=401
        )

    now = datetime.now(UTC)
    user_id = uuid.UUID(payload.sub)

    if row.revoked_at is not None:
        if _is_race_not_replay(row, request, now):
            replacement = (
                await db.execute(
                    select(RefreshToken).where(RefreshToken.id == row.replaced_by_id)
                )
            ).scalar_one_or_none()
            if (
                replacement is not None
                and replacement.revoked_at is None
                and _aware(replacement.expires_at) > now
            ):
                logger.info(
                    "Refresh-token race resolved for user_id=%s; chaining from replacement.",
                    row.user_id,
                )
                return await _chain_rotate_from_replacement(
                    db, replacement, user_id, request, now
                )
        # REPLAY — the same hash is being re-presented after rotation
        # (and it isn't a tab-race we can vouch for). Burn the entire
        # user's refresh whitelist.
        logger.warning(
            "Refresh-token replay detected for user_id=%s; revoking all active tokens.",
            row.user_id,
        )
        await _revoke_all_active(db, row.user_id)
        # The mass-revoke MUST persist even though we're about to raise.
        # The session dependency rolls back on exception, which would
        # otherwise leave the live tokens active — defeating the entire
        # point of replay detection. See ADR-016 §5.2.
        await db.commit()
        raise AppError(
            code="invalid_refresh", message="세션이 만료되었습니다", status=401
        )

    if _aware(row.expires_at) <= now:
        raise AppError(
            code="invalid_refresh", message="세션이 만료되었습니다", status=401
        )

    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise AppError(
            code="invalid_refresh", message="세션이 만료되었습니다", status=401
        )

    # Atomic rotation: revoke the row we just consumed, mint new tokens,
    # and link the chain so a concurrent presenter of this same hash can
    # be classified as race rather than replay.
    access, new_row, new_refresh, csrf = await _issue_tokens_with_row(
        db, user, request
    )
    row.revoked_at = now
    row.replaced_by_id = new_row.id
    return access, new_refresh, csrf, user


async def _chain_rotate_from_replacement(
    db: AsyncSession,
    replacement: RefreshToken,
    user_id: uuid.UUID,
    request: Request,
    now: datetime,
) -> tuple[str, str, str, User]:
    """Race resolution: rotate from the already-issued replacement.

    The losing tab presented the stale token after the winner committed.
    We mint *another* generation rather than re-emitting the winner's
    tokens (which we don't store in plaintext). The cookie store ends
    up with whichever generation the browser receives last — both are
    valid until rotated themselves.
    """

    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise AppError(
            code="invalid_refresh", message="세션이 만료되었습니다", status=401
        )
    access, new_row, new_refresh, csrf = await _issue_tokens_with_row(
        db, user, request
    )
    replacement.revoked_at = now
    replacement.replaced_by_id = new_row.id
    return access, new_refresh, csrf, user


async def revoke_refresh(db: AsyncSession, refresh_token: str) -> None:
    """Best-effort revoke for the cookie carried by /api/auth/logout.

    Silently ignores unknown / malformed tokens — logout always succeeds
    from the client's perspective; the worst-case is a stale row that
    already expired.
    """

    try:
        digest = hash_refresh_token(refresh_token)
    except Exception:  # noqa: BLE001 — never block logout
        return
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = datetime.now(UTC)
    await db.flush()
