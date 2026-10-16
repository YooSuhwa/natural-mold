"""Shared status/message contracts for staged organization authorization errors."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from app.exceptions import AppError

ErrorCode = Literal[
    "not_found",
    "permission_required",
    "stale_version",
    "organization_unavailable",
    "invalid_cursor",
    "policy_locked",
    "conflict",
    "last_admin",
    "resource_transfer_required",
    "org_access_denied",
    "catalog_policy_locked",
    "invitation_invalid",
    "invitation_used",
    "invitation_expired",
    "invitation_email_mismatch",
    "invitation_membership_blocked",
    "invitation_group_unavailable",
    "organization_bootstrap_required",
    "registration_closed",
    "registration_invitation_required",
    "AGENT_ARCHIVED",
    "AGENT_NOT_ARCHIVED",
    "AGENT_LIFECYCLE_EXECUTION_PENDING",
    "AGENT_PURGE_NAME_MISMATCH",
    "AGENT_HISTORY_SNAPSHOT_INCOMPLETE",
    "AGENT_LIFECYCLE_PERMISSION_REQUIRED",
    "duplicate_tenant_slug",
    "tenant_namespace_collision",
    "last_tenant_admin",
    "last_organization",
    "organization_archive_name_mismatch",
    "organization_restore_expired",
    "default_organization_archive_forbidden",
    "tenant_invitation_invalid",
    "tenant_invitation_used",
    "tenant_invitation_expired",
    "tenant_invitation_email_mismatch",
    "tenant_invitation_revoked",
    "tenant_status_conflict",
]


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status: int
    message: str


ERRORS: Final[Mapping[ErrorCode, ErrorSpec]] = MappingProxyType(
    {
        "not_found": ErrorSpec(404, "요청한 항목을 찾을 수 없습니다"),
        "permission_required": ErrorSpec(403, "조직 관리 권한이 필요합니다"),
        "stale_version": ErrorSpec(409, "다른 사람이 먼저 변경했습니다. 새로고침해 주세요"),
        "organization_unavailable": ErrorSpec(403, "사용할 수 없는 조직입니다"),
        "invalid_cursor": ErrorSpec(422, "목록 위치 정보가 올바르지 않습니다"),
        "policy_locked": ErrorSpec(403, "디렉터리에서 관리하는 그룹은 직접 변경할 수 없습니다"),
        "conflict": ErrorSpec(409, "이 이름의 그룹이 이미 있습니다"),
        "last_admin": ErrorSpec(409, "마지막 조직 관리자를 제거할 수 없습니다"),
        "org_access_denied": ErrorSpec(403, "현재 조직에서만 대상을 검색할 수 있습니다"),
        "catalog_policy_locked": ErrorSpec(403, "회사 또는 플랫폼에서 끈 항목은 켤 수 없습니다"),
        "invitation_invalid": ErrorSpec(404, "사용할 수 없는 초대 링크입니다"),
        "invitation_used": ErrorSpec(409, "이미 사용한 초대 링크입니다"),
        "invitation_expired": ErrorSpec(410, "초대 링크가 만료되었습니다"),
        "invitation_email_mismatch": ErrorSpec(403, "초대받은 이메일의 계정으로 로그인해 주세요"),
        "invitation_membership_blocked": ErrorSpec(
            403, "구성원 상태를 먼저 관리자에게 확인해 주세요"
        ),
        "invitation_group_unavailable": ErrorSpec(
            409, "초대 대상 그룹이 변경되었습니다. 새 초대를 요청해 주세요"
        ),
        "organization_bootstrap_required": ErrorSpec(503, "기본 회사와 조직을 초기화해야 합니다"),
        "registration_closed": ErrorSpec(403, "현재는 새 계정을 만들 수 없습니다"),
        "registration_invitation_required": ErrorSpec(403, "초대 링크로 가입해 주세요"),
        "resource_transfer_required": ErrorSpec(
            409, "소유한 자원을 먼저 이전하거나 삭제해야 합니다"
        ),
        "AGENT_ARCHIVED": ErrorSpec(409, "보관된 에이전트는 실행할 수 없습니다"),
        "AGENT_NOT_ARCHIVED": ErrorSpec(409, "보관된 에이전트만 처리할 수 있습니다"),
        "AGENT_LIFECYCLE_EXECUTION_PENDING": ErrorSpec(
            409, "에이전트 실행이 완전히 중지될 때까지 기다려 주세요"
        ),
        "AGENT_PURGE_NAME_MISMATCH": ErrorSpec(409, "에이전트 이름 확인이 일치하지 않습니다"),
        "AGENT_HISTORY_SNAPSHOT_INCOMPLETE": ErrorSpec(
            409, "대화 기록 스냅샷이 완성되지 않았습니다"
        ),
        "AGENT_LIFECYCLE_PERMISSION_REQUIRED": ErrorSpec(
            403, "에이전트 수명주기 관리 권한이 필요합니다"
        ),
        "duplicate_tenant_slug": ErrorSpec(409, "같은 회사 슬러그가 이미 있습니다"),
        "tenant_namespace_collision": ErrorSpec(500, "회사 내부 식별자 충돌이 발생했습니다"),
        "last_tenant_admin": ErrorSpec(409, "마지막 회사 관리자를 제거할 수 없습니다"),
        "last_organization": ErrorSpec(409, "마지막 조직은 보관할 수 없습니다"),
        "organization_archive_name_mismatch": ErrorSpec(409, "조직 이름 확인이 일치하지 않습니다"),
        "organization_restore_expired": ErrorSpec(410, "조직 복원 가능 기간이 만료되었습니다"),
        "default_organization_archive_forbidden": ErrorSpec(409, "기본 조직은 보관할 수 없습니다"),
        "tenant_invitation_invalid": ErrorSpec(404, "사용할 수 없는 회사 초대 링크입니다"),
        "tenant_invitation_used": ErrorSpec(409, "이미 사용한 회사 초대 링크입니다"),
        "tenant_invitation_expired": ErrorSpec(410, "회사 초대 링크가 만료되었습니다"),
        "tenant_invitation_email_mismatch": ErrorSpec(
            403, "초대받은 이메일의 계정으로 로그인해 주세요"
        ),
        "tenant_invitation_revoked": ErrorSpec(404, "사용할 수 없는 회사 초대 링크입니다"),
        "tenant_status_conflict": ErrorSpec(409, "현재 회사 상태에서는 처리할 수 없습니다"),
    }
)


def authorization_error(code: ErrorCode) -> AppError:
    spec = ERRORS[code]
    return AppError(code=code, message=spec.message, status=spec.status)


def agent_archived() -> AppError:
    return authorization_error("AGENT_ARCHIVED")


def agent_not_archived() -> AppError:
    return authorization_error("AGENT_NOT_ARCHIVED")


def agent_lifecycle_execution_pending() -> AppError:
    return authorization_error("AGENT_LIFECYCLE_EXECUTION_PENDING")


def agent_purge_name_mismatch() -> AppError:
    return authorization_error("AGENT_PURGE_NAME_MISMATCH")


def agent_history_snapshot_incomplete() -> AppError:
    return authorization_error("AGENT_HISTORY_SNAPSHOT_INCOMPLETE")


def agent_lifecycle_permission_required() -> AppError:
    return authorization_error("AGENT_LIFECYCLE_PERMISSION_REQUIRED")
