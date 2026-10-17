"""``insufficient_scope`` step-up for MCP OAuth (plan 11.4.1 / 11.5, F-15).

When an MCP server answers 401/403 with ``WWW-Authenticate: Bearer
error="insufficient_scope", scope="..."``, Moldy merges the previously
requested scope with the challenged one and raises an ``auth_required``
interrupt with ``reason=scope_upgrade`` for the credential that made the call
(in ``per_user`` mode the caller's own bound credential). The merged scope is
not stored on the credential: the connect popup passes it to
``POST /api/oauth2-credential/auth/{id}``, it rides on the OAuth state row and
replaces the granted scope only when the callback succeeds (refresh keeps
using the granted scope). The same resource and action is retried at most
twice (``MAX_STEP_UP_ATTEMPTS``).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

MAX_STEP_UP_ATTEMPTS = 2
_PARAM = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]+))')


@dataclass(frozen=True)
class ScopeChallenge:
    scope: str | None
    resource_metadata: str | None


def parse_bearer_challenge(header: str | None) -> dict[str, str]:
    """Return the auth-params of the ``Bearer`` challenge (keys lower-cased)."""

    if not header:
        return {}
    match = re.search(r"(?i)\bbearer\b", header)
    if match is None:
        return {}
    params: dict[str, str] = {}
    for key, quoted, token in _PARAM.findall(header[match.end() :]):
        params.setdefault(key.lower(), quoted or token)
    return params


def insufficient_scope_challenge(
    status_code: int, headers: Mapping[str, str]
) -> ScopeChallenge | None:
    if status_code not in (401, 403):
        return None
    header = next((v for k, v in headers.items() if k.lower() == "www-authenticate"), None)
    params = parse_bearer_challenge(header)
    if params.get("error") != "insufficient_scope":
        return None
    return ScopeChallenge(
        scope=params.get("scope") or None,
        resource_metadata=params.get("resource_metadata") or None,
    )


def merge_scopes(previous: str | None, challenged: str | None) -> str:
    merged: list[str] = []
    for part in f"{previous or ''} {challenged or ''}".split():
        if part not in merged:
            merged.append(part)
    return " ".join(merged)


@dataclass
class ScopeStepUpBudget:
    """Bounded step-up retries per (resource, action) within one run."""

    max_attempts: int = MAX_STEP_UP_ATTEMPTS
    attempts: dict[tuple[str, str], int] = field(default_factory=dict)

    def consume(self, resource: str, action: str) -> bool:
        key = (resource, action)
        used = self.attempts.get(key, 0)
        if used >= self.max_attempts:
            return False
        self.attempts[key] = used + 1
        return True


@dataclass(frozen=True)
class ScopeStepUp:
    scope: str
    interrupt: dict[str, Any]


def plan_scope_step_up(
    *,
    credential_data: Mapping[str, Any],
    challenge: ScopeChallenge,
    budget: ScopeStepUpBudget,
    resource: str,
    resource_name: str,
    tool_name: str,
    credential_id: uuid.UUID | None,
    fixable_by: Literal["me", "owner"] = "me",
) -> ScopeStepUp | None:
    """Return the merged scope and the ``auth_required`` interrupt, or None.

    None means the step-up budget for this resource and action is spent, so
    the caller must fail the call instead of asking for another authorization.
    """

    previous = credential_data.get("scope")
    scope = merge_scopes(str(previous) if previous else None, challenge.scope)
    if not budget.consume(resource, tool_name):
        return None
    item = {
        "resource_type": "mcp_server",
        "resource_name": resource_name,
        "tool_names": [tool_name],
        "credential_id": str(credential_id) if credential_id else None,
        "fixable_by": fixable_by,
        "connect": {"kind": "oauth", "scope": scope},
    }
    interrupt = {
        "type": "auth_required",
        "reason": "scope_upgrade",
        "items": [item],
        "allowed_decisions": ["respond"],
    }
    return ScopeStepUp(scope=scope, interrupt=interrupt)


__all__ = [
    "MAX_STEP_UP_ATTEMPTS",
    "ScopeChallenge",
    "ScopeStepUp",
    "ScopeStepUpBudget",
    "insufficient_scope_challenge",
    "merge_scopes",
    "parse_bearer_challenge",
    "plan_scope_step_up",
]
