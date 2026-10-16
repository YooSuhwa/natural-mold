"""Response contracts of the plan 8.4 decision table.

Every code has one registered shape in ``error_codes.ERRORS``; details are only
attached where 8.4 allows them, so a hidden resource never says which
permission was missing.
"""

from datetime import datetime
from typing import Literal

from app.authz.error_codes import authorization_error
from app.exceptions import AppError

QuotaKind = Literal["members", "monthly_spend", "concurrent_runs", "rate"]
QuotaScope = Literal["tenant", "org", "user", "agent"]


def not_found() -> AppError:
    """Default for callers without an existing per-resource not-found code."""
    return authorization_error("not_found")


def permission_required(required: str, *, request_access: bool) -> AppError:
    """Only raised after minimum visibility was confirmed."""
    return authorization_error(
        "permission_required", {"required": required, "request_access": request_access}
    )


def capability_required(capability: str) -> AppError:
    return authorization_error("capability_required", {"capability": capability})


def org_access_denied() -> AppError:
    return authorization_error("org_access_denied")


def tenant_suspended() -> AppError:
    return authorization_error("tenant_suspended")


def quota_exceeded(
    kind: QuotaKind,
    scope: QuotaScope,
    *,
    limit: int | float | None = None,
    used: int | float | None = None,
    resets_at: datetime | None = None,
) -> AppError:
    details: dict[str, object] = {"kind": kind, "scope": scope}
    optional = {"limit": limit, "used": used, "resets_at": resets_at}
    details.update({key: value for key, value in optional.items() if value is not None})
    return authorization_error("quota_exceeded", details)


def authz_unavailable() -> AppError:
    return authorization_error("authz_unavailable")
