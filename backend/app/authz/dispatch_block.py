"""Typed BLOCKED reasons of non-interactive runs (plan 11.1, 11.6; F-08).

Scheduled triggers, queued inputs, Agent API keys and subagent delegations are
decided at dispatch with the 11.1 subject. A denial is not a new status: the run
ends ``failed`` with a ``reason_code`` (triggers keep the consecutive-failure
auto pause of ``finish_trigger_run``). :func:`block_reason` maps the denial an
entry raised (anywhere in the exception chain) to that reason; ``None`` means the
error is not an authorization block (the entry keeps its existing handling).

``authz_unavailable`` (OpenFGA down) is transient: triggers record it as
``skipped`` so it never counts toward the auto pause (backoff = the next fire).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

from app.exceptions import AppError

BlockReason = Literal[
    "permission_revoked",
    "owner_left",
    "org_archived",
    "tenant_suspended",
    "agent_archived",
    "delegation_broken",
    "required_unavailable",
    "auth_required",
    "authz_unavailable",
]
TRANSIENT: Final[frozenset[str]] = frozenset({"authz_unavailable"})
_BY_CODE: Final[Mapping[str, BlockReason]] = {
    "permission_required": "permission_revoked",
    "not_found": "permission_revoked",
    "AGENT_NOT_FOUND": "permission_revoked",
    "TRIGGER_NOT_FOUND": "permission_revoked",
    "AGENT_DEPLOYMENT_NOT_FOUND": "permission_revoked",
    "organization_unavailable": "org_archived",
    "organization_archived": "org_archived",
    "tenant_suspended": "tenant_suspended",
    "AGENT_ARCHIVED": "agent_archived",
    "agent_not_ready": "required_unavailable",
    "credential_binding_required": "auth_required",
    "authz_unavailable": "authz_unavailable",
}
_OWNER_CODES: Final[Mapping[str, BlockReason]] = {
    "organization_unavailable": "org_archived",
    "organization_archived": "org_archived",
    "tenant_suspended": "tenant_suspended",
}
_MAX_CHAIN: Final = 8


class DispatchBlockedError(AppError):
    """An ``AppError`` (same code/status/body) that already knows its block reason."""

    def __init__(self, reason: BlockReason, error: AppError) -> None:
        super().__init__(error.code, error.message, error.status, error.details)
        self.reason: BlockReason = reason


def owner_unavailable(error: AppError) -> DispatchBlockedError:
    """The run owner's account/membership no longer resolves in the run's org.

    The only source of ``owner_left``: a generic ``forbidden``/``not_found`` elsewhere
    is never read as the owner leaving (typed where the owner is resolved)."""
    return DispatchBlockedError(_OWNER_CODES.get(error.code, "owner_left"), error)


def _not_ready_reason(details: object) -> BlockReason:
    items = details.get("items") if isinstance(details, Mapping) else None
    broken = isinstance(items, list) and any(
        isinstance(item, Mapping) and item.get("result") == "delegation_broken" for item in items
    )
    return "delegation_broken" if broken else "required_unavailable"


def _one(error: BaseException) -> tuple[BlockReason | None, bool]:
    """(reason, settled): ``agent_not_ready`` without details looks deeper."""
    if isinstance(error, DispatchBlockedError):
        return error.reason, True
    code = getattr(error, "code", None)
    if not isinstance(code, str) or code not in _BY_CODE:
        return None, False
    if code == "agent_not_ready":
        details = getattr(error, "details", None)
        return _not_ready_reason(details), details is not None
    return _BY_CODE[code], True


def block_reason(error: BaseException | None) -> BlockReason | None:
    """The BLOCKED reason of ``error`` or of any exception it was raised from."""
    fallback: BlockReason | None = None
    seen: list[BaseException] = []
    while error is not None and len(seen) < _MAX_CHAIN and error not in seen:
        seen.append(error)
        reason, settled = _one(error)
        if settled:
            return reason
        fallback = fallback or reason
        error = error.__cause__ or error.__context__
    return fallback


def blocked_message(reason: str, error: BaseException | None) -> str:
    """Trigger-run ``error_message``: the reason plus the machine code, no free text."""
    code = getattr(error, "code", None)
    return f"{reason}: {code}" if isinstance(code, str) and code else reason


__all__ = [
    "TRANSIENT",
    "BlockReason",
    "DispatchBlockedError",
    "block_reason",
    "blocked_message",
    "owner_unavailable",
]
