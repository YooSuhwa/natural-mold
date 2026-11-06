"""Run-scoped tokens for calls to the MCP runtime (plan §7.3).

The backend signs one per run and server: who runs it, which run, agent, company and
organization, and which runtime server it is for, valid five minutes at most. The secret
(``MCP_RUNTIME_TOKEN_SECRET``) is shared with the runtime only, never the session
``JWT_SECRET``, so the runtime cannot mint session tokens. The runtime's verifier is
``mcp-runtime/moldy_mcp_runtime/tokens.py``; both pin the same claims and limits.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Final

import jwt

from app.config import settings

ISSUER: Final = "moldy-backend"
AUDIENCE: Final = "moldy-mcp-runtime"
ALGORITHM: Final = "HS256"
MAX_LIFETIME_SECONDS: Final = 300
MIN_SECRET_BYTES: Final = 32


class RuntimeTokenUnavailable(RuntimeError):
    """The runtime is not configured (no URL or no secret)."""


@dataclass(frozen=True, slots=True)
class RunIdentity:
    actor_id: uuid.UUID
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    org_id: uuid.UUID
    agent_id: uuid.UUID | None = None


def _secret() -> str:
    value = settings.mcp_runtime_token_secret
    if len(value.encode()) < MIN_SECRET_BYTES:
        raise RuntimeTokenUnavailable("MCP_RUNTIME_TOKEN_SECRET is not set (32 bytes or more)")
    return value


def issue(
    server: str, identity: RunIdentity, *, lifetime_seconds: int = MAX_LIFETIME_SECONDS
) -> str:
    """A token for one runtime server path (``openapi/<id>``, ``db/<id>``, ...)."""
    if not 0 < lifetime_seconds <= MAX_LIFETIME_SECONDS:
        raise ValueError(f"runtime tokens live 1-{MAX_LIFETIME_SECONDS} seconds")
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(identity.actor_id),
        "iat": now,
        "exp": now + lifetime_seconds,
        "jti": str(uuid.uuid4()),
        "run_id": str(identity.run_id),
        "tenant_id": str(identity.tenant_id),
        "org_id": str(identity.org_id),
        "server": server,
    }
    if identity.agent_id is not None:
        claims["agent_id"] = str(identity.agent_id)
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)
