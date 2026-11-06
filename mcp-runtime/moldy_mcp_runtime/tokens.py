"""Run-scoped tokens the backend signs for each run that calls the runtime (plan §7.3).

HS256 with a secret only the backend and the runtime hold (``MCP_RUNTIME_TOKEN_SECRET``,
never the session ``JWT_SECRET``). The backend side is ``app.mcp_runtime.tokens``; both
pin the same issuer, audience, claims and five-minute ceiling.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Final

import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ISSUER: Final = "moldy-backend"
AUDIENCE: Final = "moldy-mcp-runtime"
ALGORITHM: Final = "HS256"
MAX_LIFETIME_SECONDS: Final = 300
MIN_SECRET_BYTES: Final = 32
_REQUIRED: Final = (
    "iss",
    "aud",
    "sub",
    "exp",
    "iat",
    "jti",
    "run_id",
    "tenant_id",
    "org_id",
    "server",
)


class TokenError(Exception):
    """The token is missing, malformed, expired or not for this runtime (no detail leaks)."""


@dataclass(frozen=True, slots=True)
class RunClaims:
    actor_id: str
    run_id: str
    agent_id: str | None
    tenant_id: str
    org_id: str
    server: str
    token_id: str
    # Relay tokens only: the sealed upstream (``open_upstream``).
    upstream: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "org_id": self.org_id,
            "server": self.server,
        }


def secret() -> str:
    value = os.environ.get("MCP_RUNTIME_TOKEN_SECRET", "")
    if len(value.encode()) < MIN_SECRET_BYTES:
        raise RuntimeError("MCP_RUNTIME_TOKEN_SECRET must be set (32 bytes or more)")
    return value


def verify(token: str, *, server: str, key: str | None = None) -> RunClaims:
    """The claims of a token made for ``server``; anything else raises ``TokenError``."""
    try:
        claims = jwt.decode(
            token,
            key or secret(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": list(_REQUIRED)},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if int(claims["exp"]) - int(claims["iat"]) > MAX_LIFETIME_SECONDS:
        raise TokenError("token lifetime too long")
    if claims["server"] != server:
        raise TokenError("token is for another server")
    for name in ("sub", "run_id", "tenant_id", "org_id", "jti"):
        try:
            uuid.UUID(str(claims[name]))
        except ValueError as exc:
            raise TokenError("malformed claim") from exc
    return RunClaims(
        actor_id=str(claims["sub"]),
        run_id=str(claims["run_id"]),
        agent_id=str(claims["agent_id"]) if claims.get("agent_id") else None,
        tenant_id=str(claims["tenant_id"]),
        org_id=str(claims["org_id"]),
        server=str(claims["server"]),
        token_id=str(claims["jti"]),
        upstream=str(claims["upstream"]) if isinstance(claims.get("upstream"), str) else None,
    )


# --- sealed upstream (relay tokens) --------------------------------------------------
# The backend seals the upstream server's URL and headers (credentials included) so they
# are never readable in the token: AES-256-GCM, a key derived from the shared secret
# (HKDF-SHA256, its own ``info``), the token id as associated data. Same scheme as
# ``app.mcp_runtime.tokens`` in the backend.
_RELAY_INFO: Final = b"moldy-mcp-relay-v1"
_NONCE_BYTES: Final = 12


def _relay_key(key: str | None) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_RELAY_INFO).derive(
        (key or secret()).encode()
    )


def open_upstream(blob: str, token_id: str, key: str | None = None) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
        nonce, sealed = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        opened = AESGCM(_relay_key(key)).decrypt(nonce, sealed, token_id.encode())
        value = json.loads(opened)
    except (ValueError, InvalidTag) as exc:
        raise TokenError("invalid upstream") from exc
    if not isinstance(value, dict):
        raise TokenError("invalid upstream")
    return value
