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


def _invalid_refresh() -> AppError:
    return AppError(
        code="invalid_refresh", message="세션이 만료되었습니다", status=401
    )


async def _find_race_chain_head(
    db: AsyncSession, row: RefreshToken, request: Request, now: datetime
) -> RefreshToken | None:
    """Resolve a revoked row to its live chain head if this is a tab-race.

    Returns the active replacement when ALL of the following hold; the
    caller chain-rotates from it instead of triggering mass-revoke:

    1. ``row.replaced_by_id`` is set (row was *rotated*, not logged out
       or mass-revoked).
    2. The revocation is within ``settings.refresh_rotation_grace_seconds``.
    3. The originating user-agent matches the current request — a cheap
       binding that blocks a stolen-cookie attacker on a different
       browser. Not cryptographic, but materially raises the bar over
       "any presenter of the stale token wins".
    4. The replacement row itself is still active and unexpired.

    Any other revoked-row presentation returns ``None`` ⇒ caller treats
    it as a replay attack and burns the user's whitelist.
    """

    if row.replaced_by_id is None or row.revoked_at is None:
        return None
    grace = timedelta(seconds=settings.refresh_rotation_grace_seconds)
    if grace.total_seconds() <= 0:
        return None
    if now - _aware(row.revoked_at) > grace:
        return None
    if (row.user_agent or "") != (user_agent(request) or ""):
        return None
    replacement = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.id == row.replaced_by_id)
        )
    ).scalar_one_or_none()
    if replacement is None or replacement.revoked_at is not None:
        return None
    if _aware(replacement.expires_at) <= now:
        return None
    return replacement


async def _load_active_user_or_401(
    db: AsyncSession, user_id: uuid.UUID
) -> User:
    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise _invalid_refresh()
    return user


async def _perform_rotation(
    db: AsyncSession,
    old_row: RefreshToken,
    user: User,
    request: Request,
    now: datetime,
) -> tuple[str, str, str]:
    """Mint a new token leg, revoke ``old_row``, link the chain.

    Caller is responsible for ensuring ``old_row`` is the locked +
    re-verified-active result of ``_lock_active_row`` — otherwise a
    concurrent rotation from the same row could orphan a leg
    (see :func:`rotate_refresh` chain-walk loop).
    """

    access, new_row, new_refresh, csrf = await _issue_tokens_with_row(
        db, user, request
    )
    old_row.revoked_at = now
    old_row.replaced_by_id = new_row.id
    return access, new_refresh, csrf


async def _lock_row(db: AsyncSession, row_id: uuid.UUID) -> RefreshToken | None:
    """Re-fetch ``row_id`` with a row-level lock so concurrent rotations
    from the same row serialise.

    Postgres uses ``SELECT ... FOR UPDATE``; SQLite (test path) ignores
    the hint, which is fine because tests don't run concurrent writers.
    Returns ``None`` if the row was deleted (e.g. GC) between selects.
    """

    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    stmt = select(RefreshToken).where(RefreshToken.id == row_id)
    if dialect == "postgresql":
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


# Maximum forward jumps through the chain when a tab race forces us to
# rotate from a replacement that's itself been rotated. Five is far above
# any plausible concurrency burst — a deeper chain almost certainly means
# something pathological (storm of stale-token presentations or coding
# bug). Each iteration costs one row lock + one SELECT.
_MAX_CHAIN_FOLLOW = 5


async def rotate_refresh(
    db: AsyncSession, refresh_token: str, request: Request
) -> tuple[str, str, str, User]:
    """Validate + rotate a refresh token.

    Three outcomes for a candidate row at each chain step:

    * **Live** — normal path: lock the row, revoke it, mint replacement,
      link ``old.replaced_by_id``.
    * **Race** — row is revoked but its replacement is still active and
      the request looks like a tab-race (see :func:`_find_race_chain_head`).
      Follow the chain forward and retry. Bounded by ``_MAX_CHAIN_FOLLOW``.
    * **Replay** — row is revoked with no eligible race chain. Burn the
      user's entire refresh whitelist (force re-login) and 401.

    The chain-walk loop is what makes concurrent rotations from the same
    row safe: ``_lock_row`` serialises writers on Postgres so the loser
    of a race observes the winner's revocation and follows the chain
    instead of producing an orphaned leg (HANDOFF #2b).

    Returns ``(access, refresh, csrf, user)`` so the router can set
    cookies and respond with the latest user projection.
    """

    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except InvalidTokenError as exc:
        raise _invalid_refresh() from exc

    digest = hash_refresh_token(refresh_token)
    candidate = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if candidate is None:
        # Hash unknown — token forged or already GC'd.
        raise _invalid_refresh()

    user_id = uuid.UUID(payload.sub)

    for _ in range(_MAX_CHAIN_FOLLOW):
        locked = await _lock_row(db, candidate.id)
        if locked is None:
            # Row vanished between the initial lookup and the lock —
            # treat as an unknown hash.
            raise _invalid_refresh()
        now = datetime.now(UTC)

        if locked.revoked_at is None:
            # Live: rotate this row.
            if _aware(locked.expires_at) <= now:
                raise _invalid_refresh()
            user = await _load_active_user_or_401(db, user_id)
            access, new_refresh, csrf = await _perform_rotation(
                db, locked, user, request, now
            )
            return access, new_refresh, csrf, user

        # Revoked: race-chain or replay?
        chain_head = await _find_race_chain_head(db, locked, request, now)
        if chain_head is None:
            logger.warning(
                "Refresh-token replay detected for user_id=%s; revoking all active tokens.",
                locked.user_id,
            )
            await _revoke_all_active(db, locked.user_id)
            # Mass-revoke MUST persist even though we're about to raise.
            # The session dependency rolls back on exception, which would
            # otherwise leave the live tokens active. See ADR-016 §5.2.
            await db.commit()
            raise _invalid_refresh()

        logger.info(
            "Refresh-token race resolved for user_id=%s; chaining from replacement.",
            locked.user_id,
        )
        candidate = chain_head

    # Followed the chain to its bound without finding a live row — refuse
    # rather than spin forever.
    logger.warning(
        "Refresh-token chain follow exhausted for user_id=%s after %d hops",
        candidate.user_id,
        _MAX_CHAIN_FOLLOW,
    )
    raise _invalid_refresh()


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
