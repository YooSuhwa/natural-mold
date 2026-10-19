"""Secret-free structural diagnostics for native invitation races."""

from __future__ import annotations

from typing import Final, Literal

from sqlalchemy.exc import DBAPIError, IntegrityError, InvalidRequestError, OperationalError

from app.exceptions import AppError


class NativeInvitationAcceptResumeStageError(AssertionError):
    pass


class NativeInvitationAcceptServiceStageError(AssertionError):
    pass


class NativeInvitationAcceptCommitStageError(AssertionError):
    pass


class NativeInvitationRemovalServiceStageError(AssertionError):
    pass


class NativeInvitationRemovalCommitStageError(AssertionError):
    pass


class NativeInvitationBarrierTimeoutStageError(AssertionError):
    pass


class NativeInvitationBarrierUnknownStageError(AssertionError):
    pass


class NativeInvitationCompanyStateStageError(AssertionError):
    pass


class NativeInvitationOrganizationStateStageError(AssertionError):
    pass


class NativeInvitationTenantRoleStateStageError(AssertionError):
    pass


class NativeInvitationHistoryStateStageError(AssertionError):
    pass


class NativeInvitationCleanupStageError(AssertionError):
    pass


type NativeInvitationFailurePhase = Literal[
    "AcceptService",
    "AcceptCommit",
    "RemovalService",
    "RemovalCommit",
]

_APP_ERROR_TYPE_LABELS: Final = {
    "AppError": "AppError",
    "NotFoundError": "NotFoundError",
    "ValidationError": "ValidationError",
    "ConflictError": "ConflictError",
    "ForbiddenError": "ForbiddenError",
    "ExternalServiceError": "ExternalServiceError",
}

_APP_ERROR_CODE_LABELS: Final = {
    "not_found": "NotFound",
    "permission_required": "PermissionRequired",
    "organization_unavailable": "OrganizationUnavailable",
    "policy_locked": "PolicyLocked",
    "last_admin": "LastAdmin",
    "resource_transfer_required": "ResourceTransferRequired",
    "invitation_invalid": "InvitationInvalid",
    "invitation_used": "InvitationUsed",
    "invitation_expired": "InvitationExpired",
    "invitation_email_mismatch": "InvitationEmailMismatch",
    "invitation_membership_blocked": "InvitationMembershipBlocked",
    "invitation_group_unavailable": "InvitationGroupUnavailable",
    "last_tenant_admin": "LastTenantAdmin",
    "tenant_suspended": "TenantSuspended",
    "tenant_unavailable": "TenantUnavailable",
}

_DBAPI_SQLSTATE_LABELS: Final = {
    "23503": "Sqlstate23503",
    "23505": "Sqlstate23505",
    "40P01": "Sqlstate40P01",
    "55P03": "Sqlstate55P03",
    "57014": "Sqlstate57014",
}

_STANDARD_EXCEPTION_TYPE_LABELS: Final = {
    "AssertionError": "AssertionError",
    "InvalidRequestError": "InvalidRequestError",
    "LookupError": "LookupError",
    "RuntimeError": "RuntimeError",
    "TypeError": "TypeError",
    "ValueError": "ValueError",
}


def _native_invitation_cause_label(error: Exception) -> str:
    if isinstance(error, AppError):
        error_type = _APP_ERROR_TYPE_LABELS.get(type(error).__name__, "UnknownAppError")
        error_code = _APP_ERROR_CODE_LABELS.get(error.code, "UnknownCode")
        return f"{error_type}{error_code}"
    if isinstance(error, DBAPIError):
        if isinstance(error, IntegrityError):
            error_type = "IntegrityError"
        elif isinstance(error, OperationalError):
            error_type = "OperationalError"
        else:
            error_type = "DBAPIError"
        raw_sqlstate = getattr(error.orig, "sqlstate", None)
        sqlstate = (
            _DBAPI_SQLSTATE_LABELS.get(raw_sqlstate, "UnknownSqlstate")
            if isinstance(raw_sqlstate, str)
            else "UnknownSqlstate"
        )
        return f"{error_type}{sqlstate}"
    if isinstance(error, InvalidRequestError):
        return "InvalidRequestError"
    return _STANDARD_EXCEPTION_TYPE_LABELS.get(type(error).__name__, "UnexpectedError")


def _native_invitation_failure(
    phase: NativeInvitationFailurePhase, error: Exception
) -> AssertionError:
    cause = _native_invitation_cause_label(error)
    diagnostic_type = type(
        f"NativeInvitation{phase}{cause}DiagnosticError",
        (AssertionError,),
        {"__module__": __name__},
    )
    return diagnostic_type()
