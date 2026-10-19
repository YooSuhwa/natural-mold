"""F-08 (plan 11.6): typed BLOCKED reasons from dispatch denials."""

import pytest

from app.authz.dispatch_block import (
    DispatchBlockedError,
    block_reason,
    blocked_message,
    owner_unavailable,
)
from app.authz.error_codes import authorization_error
from app.error_codes import agent_not_found
from app.exceptions import AppError


def _not_ready(result: str) -> AppError:
    item = {"kind": "tool", "agent_id": "a", "required": True, "result": result, "reason": "r"}
    return authorization_error("agent_not_ready", {"overall": "blocked", "items": [item]})


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (agent_not_found(), "permission_revoked"),
        (authorization_error("permission_required"), "permission_revoked"),
        (_not_ready("permission_denied"), "required_unavailable"),
        (_not_ready("delegation_broken"), "delegation_broken"),
        (authorization_error("credential_binding_required"), "auth_required"),
        (authorization_error("authz_unavailable"), "authz_unavailable"),
        (authorization_error("organization_unavailable"), "org_archived"),
        (authorization_error("AGENT_ARCHIVED"), "agent_archived"),
        (AppError("CONVERSATION_NOT_FOUND", "x", 404), None),
        (AppError("forbidden", "x", 403), None),  # owner_left only from owner resolution
        (RuntimeError("boom"), None),
    ],
)
def test_reason_by_code(error: BaseException, reason: str | None) -> None:
    assert block_reason(error) == reason


def test_owner_resolution_failures_are_owner_reasons() -> None:
    left = owner_unavailable(AppError("not_found", "gone", 404))
    assert isinstance(left, DispatchBlockedError) and (left.code, left.status) == ("not_found", 404)
    assert block_reason(left) == "owner_left"
    archived = owner_unavailable(authorization_error("organization_unavailable"))
    assert block_reason(archived) == "org_archived"


def test_chain_prefers_the_detailed_not_ready_cause() -> None:
    class Wrapped(RuntimeError):
        code = "agent_not_ready"

    try:
        try:
            raise _not_ready("delegation_broken")
        except AppError as cause:
            raise Wrapped("agent_not_ready") from cause
    except Wrapped as wrapped:
        assert block_reason(wrapped) == "delegation_broken"
    assert block_reason(Wrapped()) == "required_unavailable"  # no detailed cause


def test_message_carries_only_machine_codes() -> None:
    assert blocked_message("owner_left", AppError("forbidden", "권한이 없습니다", 403)) == (
        "owner_left: forbidden"
    )
    assert blocked_message("delegation_broken", None) == "delegation_broken"
