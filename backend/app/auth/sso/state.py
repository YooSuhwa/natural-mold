"""OIDC login transaction (state, nonce, PKCE verifier) bound to the browser.

The transaction rides in a short-lived HttpOnly cookie signed with the JWT secret
(audience ``moldy-sso-state``) and scoped to ``/auth/callback``; the callback accepts
the IdP's ``state`` only when it equals the cookie's (plan 9.7: "콜백은 CSRF 대신 state
검증"). ``mode=test`` carries the administrator who started a test login.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Literal
from uuid import UUID

from fastapi import Response

from app.auth.jwt import decode_transaction, encode_transaction
from app.config import settings

STATE_COOKIE: Final = "moldy_sso_state"
LOGIN_CSRF_COOKIE: Final = "moldy_login_csrf"
CALLBACK_PATH: Final = "/auth/callback"
_AUDIENCE: Final = "moldy-sso-state"
_TTL: Final = timedelta(minutes=10)
Mode = Literal["login", "test"]


@dataclass(frozen=True, slots=True)
class LoginTransaction:
    state: str
    nonce: str
    verifier: str
    connection_id: UUID
    mode: Mode
    admin_id: UUID | None = None

    @property
    def challenge(self) -> str:
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_transaction(
    connection_id: UUID, mode: Mode = "login", admin_id: UUID | None = None
) -> LoginTransaction:
    return LoginTransaction(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(64),
        connection_id=connection_id,
        mode=mode,
        admin_id=admin_id,
    )


def set_transaction_cookie(response: Response, transaction: LoginTransaction) -> None:
    claims: dict[str, object] = {
        "st": transaction.state,
        "nn": transaction.nonce,
        "cv": transaction.verifier,
        "cid": str(transaction.connection_id),
        "md": transaction.mode,
        "adm": str(transaction.admin_id) if transaction.admin_id else None,
    }
    response.set_cookie(
        STATE_COOKIE,
        encode_transaction(claims, audience=_AUDIENCE, ttl=_TTL),
        max_age=int(_TTL.total_seconds()),
        path=CALLBACK_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",  # the IdP returns with a top-level cross-site GET
    )


def read_transaction(cookie: str | None, state: str | None) -> LoginTransaction | None:
    """The cookie's transaction when it is genuine, unexpired and matches ``state``."""
    if not cookie or not state:
        return None
    claims = decode_transaction(cookie, audience=_AUDIENCE)
    if claims is None:
        return None
    try:
        transaction = LoginTransaction(
            state=str(claims["st"]),
            nonce=str(claims["nn"]),
            verifier=str(claims["cv"]),
            connection_id=UUID(str(claims["cid"])),
            mode="test" if claims["md"] == "test" else "login",
            admin_id=UUID(str(claims["adm"])) if claims.get("adm") else None,
        )
    except (KeyError, ValueError):
        return None
    if not secrets.compare_digest(transaction.state, state):
        return None
    return transaction


def clear_transaction_cookie(response: Response) -> None:
    response.delete_cookie(STATE_COOKIE, path=CALLBACK_PATH)


def frontend_url(path: str) -> str:
    """Absolute frontend URL (first CORS origin; relative when none is configured)."""
    origins = settings.cors_origins_list
    return (origins[0].rstrip("/") if origins else "") + path
