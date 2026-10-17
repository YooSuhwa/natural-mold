"""Typed MCP authorization errors (plan 11.4, 11.4.1; F-12).

The MCP transports (``streamable_http``/``sse`` over httpx) surface an HTTP 401/403
as ``httpx.HTTPStatusError`` — usually wrapped in the anyio task group's
``ExceptionGroup`` and the adapter's own re-raise. ``classify_mcp_error`` walks
that chain and returns a typed ``McpAuthError`` so the runtime can tell an
authorization problem from a transport failure:

* 401 → ``auth_required`` (``reason=expired`` for ``error="invalid_token"``,
  otherwise ``auth_needed``),
* 401/403 with ``WWW-Authenticate: Bearer error="insufficient_scope"`` →
  ``insufficient_scope`` (scope step-up, F-15),
* any other 403 → ``permission_denied``.

Everything else (connect/read errors, timeouts, 5xx, JSON-RPC errors) is a
transport error and keeps the bounded retry path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import httpx

from app.credentials.mcp_oauth_step_up import (
    ScopeChallenge,
    insufficient_scope_challenge,
    parse_bearer_challenge,
)

McpAuthKind = Literal["auth_required", "insufficient_scope", "permission_denied"]
_MAX_DEPTH = 16


class McpAuthError(Exception):
    """An MCP server refused the call's authorization (HTTP 401/403)."""

    def __init__(
        self,
        kind: McpAuthKind,
        status_code: int,
        *,
        error_code: str | None = None,
        challenge: ScopeChallenge | None = None,
    ) -> None:
        super().__init__(f"{kind} (HTTP {status_code})")
        self.kind: McpAuthKind = kind
        self.status_code = status_code
        self.error_code = error_code
        self.challenge = challenge

    @property
    def reason(self) -> Literal["auth_needed", "expired", "scope_upgrade"]:
        """The 11.5 ``auth_required`` interrupt reason for this error."""

        if self.kind == "insufficient_scope":
            return "scope_upgrade"
        return "expired" if self.error_code == "invalid_token" else "auth_needed"


def _children(exc: BaseException) -> list[BaseException]:
    children: list[BaseException] = []
    if isinstance(exc, BaseExceptionGroup):
        children.extend(exc.exceptions)
    for linked in (exc.__cause__, exc.__context__):
        if linked is not None:
            children.append(linked)
    return children


def find_http_status_error(exc: BaseException) -> httpx.HTTPStatusError | None:
    """The first ``HTTPStatusError`` in ``exc``'s group/cause/context tree."""

    stack: list[tuple[BaseException, int]] = [(exc, 0)]
    seen: set[int] = set()
    while stack:
        current, depth = stack.pop(0)
        if id(current) in seen or depth > _MAX_DEPTH:
            continue
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            return current
        stack.extend((child, depth + 1) for child in _children(current))
    return None


def auth_error_from_response(status_code: int, headers: Mapping[str, str]) -> McpAuthError | None:
    if status_code not in (401, 403):
        return None
    challenge = insufficient_scope_challenge(status_code, headers)
    header = next((v for k, v in headers.items() if k.lower() == "www-authenticate"), None)
    error_code = parse_bearer_challenge(header).get("error") or None
    if challenge is not None:
        return McpAuthError(
            "insufficient_scope", status_code, error_code=error_code, challenge=challenge
        )
    kind: McpAuthKind = "auth_required" if status_code == 401 else "permission_denied"
    return McpAuthError(kind, status_code, error_code=error_code)


def classify_mcp_error(exc: BaseException) -> McpAuthError | None:
    """``McpAuthError`` for a 401/403 anywhere in ``exc``; ``None`` = transport error."""

    if isinstance(exc, McpAuthError):
        return exc
    found = find_http_status_error(exc)
    if found is None:
        return None
    response = found.response
    return auth_error_from_response(response.status_code, response.headers)


__all__ = [
    "McpAuthError",
    "McpAuthKind",
    "auth_error_from_response",
    "classify_mcp_error",
    "find_http_status_error",
]
