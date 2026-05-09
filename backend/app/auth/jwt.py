"""JWT helpers — access / refresh / CSRF token creation + decode.

ADR-016 §3.1 — HS256, single backend secret. We issue three token kinds
with disjoint ``type`` claims so a CSRF token cannot be used in place of
an access token (or vice-versa). Decode validates the expected type.

The refresh token's ``jti`` is hashed (SHA-256) and stored in
``refresh_tokens.token_hash``. Verification compares the hash, so a DB
leak does not expose usable tokens.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt as pyjwt
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

TokenType = Literal["access", "refresh", "csrf"]


class InvalidTokenError(Exception):
    """Raised when a JWT fails decode/verification or has the wrong type."""


class TokenPayload(BaseModel):
    """Decoded JWT payload — common shape across all three token types."""

    sub: str = Field(..., description="user id (UUID string)")
    type: TokenType
    exp: int
    iat: int
    jti: str
    is_super_user: bool = False


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------


def _resolve_secret() -> str:
    secret = settings.jwt_secret
    if secret:
        return secret
    # Dev fallback — ephemeral key, regenerated each process restart. We
    # log at WARNING so the operator notices in any production run; tests
    # silence the warning via the ``caplog`` filter when desired.
    if not getattr(_resolve_secret, "_warned", False):
        logger.warning(
            "JWT_SECRET is empty — generating an ephemeral key. Tokens "
            "issued by this process will be invalidated on restart. "
            "Set JWT_SECRET (>= 32 bytes) in .env for stable sessions."
        )
        _resolve_secret._warned = True  # type: ignore[attr-defined]
    if not getattr(_resolve_secret, "_ephemeral", None):
        _resolve_secret._ephemeral = secrets.token_urlsafe(48)  # type: ignore[attr-defined]
    return _resolve_secret._ephemeral  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


def _encode(payload: dict[str, object]) -> str:
    return pyjwt.encode(payload, _resolve_secret(), algorithm=settings.jwt_algorithm)


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(user_id: uuid.UUID, *, is_super_user: bool = False) -> str:
    now = _now()
    exp = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "type": "access",
        "is_super_user": is_super_user,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return _encode(payload)


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, str, str]:
    """Mint a refresh token.

    Returns ``(token, jti, sha256_hex)`` so the caller can persist the
    hash without re-hashing the encoded string.
    """

    now = _now()
    exp = now + timedelta(days=settings.refresh_token_expire_days)
    jti = uuid.uuid4().hex
    payload: dict[str, object] = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": jti,
    }
    token = _encode(payload)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token, jti, digest


def create_csrf_token(user_id: uuid.UUID) -> str:
    now = _now()
    exp = now + timedelta(minutes=settings.csrf_token_expire_minutes)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "type": "csrf",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return _encode(payload)


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------


def decode_token(token: str, *, expected_type: TokenType) -> TokenPayload:
    """Decode + validate ``token``. Raises :class:`InvalidTokenError`."""

    try:
        raw = pyjwt.decode(
            token,
            _resolve_secret(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "type", "jti"]},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token expired") from exc
    except pyjwt.InvalidTokenError as exc:
        raise InvalidTokenError("invalid token") from exc

    if raw.get("type") != expected_type:
        raise InvalidTokenError(
            f"wrong token type: expected {expected_type}, got {raw.get('type')!r}"
        )

    try:
        return TokenPayload.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — narrow to validation
        raise InvalidTokenError(f"payload validation failed: {exc}") from exc


def hash_refresh_token(token: str) -> str:
    """Return the SHA-256 hex digest used as the DB whitelist key."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
