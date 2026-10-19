"""Shared status/message contracts for staged organization authorization errors."""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from app.exceptions import AppError

logger = logging.getLogger(__name__)

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
    "tenant_export_in_progress",
    "tenant_deletion_slug_mismatch",
    "capability_required",
    "grant_invalid",
    "confirm_required",
    "transfer_name_conflict",
    "transfer_blocked",
    "personal_account_shared",
    "credential_mode_not_per_user",
    "credential_type_mismatch",
    "credential_mode_invalid",
    "credential_reshare_blocked",
    "credential_binding_required",
    "agent_not_ready",
    "tenant_suspended",
    "quota_exceeded",
    "limit_exceeds_parent",
    "authz_unavailable",
    "policy_disabled",
    "tenant_publication_invalid",
    "access_request_closed",
    "authz_reconcile_running",
    "authz_sync_disabled",
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
        "tenant_export_in_progress": ErrorSpec(409, "이미 진행 중인 내보내기가 있습니다"),
        "tenant_deletion_slug_mismatch": ErrorSpec(409, "회사 슬러그 확인이 일치하지 않습니다"),
        "capability_required": ErrorSpec(403, "이 기능을 사용할 권한이 없습니다"),
        "grant_invalid": ErrorSpec(422, "이 대상에게 이 권한을 줄 수 없습니다"),
        "confirm_required": ErrorSpec(409, "위임된 계정 자원을 확인한 뒤 다시 공유해 주세요"),
        "transfer_blocked": ErrorSpec(
            409, "이전 소유자에게 묶인 항목을 먼저 정리해야 이전할 수 있습니다"
        ),
        "personal_account_shared": ErrorSpec(
            409,
            "개인 계정 자격 증명은 다른 사람과 공유할 수 없습니다. "
            "도구·MCP 서버는 연결 방식을 사용자별 연결로 바꾸면 공유할 수 있습니다",
        ),
        "credential_mode_not_per_user": ErrorSpec(
            409, "사용자별 연결 방식인 도구·MCP 서버에만 내 계정을 연결할 수 있습니다"
        ),
        "credential_type_mismatch": ErrorSpec(
            422, "이 도구·MCP 서버에 연결할 수 없는 자격 증명 유형입니다"
        ),
        "credential_mode_invalid": ErrorSpec(422, "연결 방식과 자격 증명 설정이 맞지 않습니다"),
        "credential_binding_required": ErrorSpec(
            409, "사용자별 연결이 필요합니다. 내 계정을 연결한 뒤 다시 시도해 주세요"
        ),
        "agent_not_ready": ErrorSpec(
            409, "에이전트의 필수 연결을 사용할 수 없습니다. 연결 상태를 확인해 주세요"
        ),
        "credential_reshare_blocked": ErrorSpec(
            409, "다른 사람의 자격 증명은 공용 방식으로 되돌릴 수 없습니다"
        ),
        "transfer_name_conflict": ErrorSpec(
            409, "새 소유자에게 같은 이름의 자원이 있습니다. 이름을 바꾼 뒤 이전하세요"
        ),
        "tenant_suspended": ErrorSpec(403, "회사가 정지되었습니다"),
        "quota_exceeded": ErrorSpec(429, "사용 한도를 초과했습니다"),
        "limit_exceeds_parent": ErrorSpec(422, "상위 한도보다 큰 한도는 정할 수 없습니다"),
        "authz_unavailable": ErrorSpec(
            503, "권한을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요"
        ),
        "policy_disabled": ErrorSpec(403, "회사 정책상 회사 전체 공개를 할 수 없습니다"),
        "tenant_publication_invalid": ErrorSpec(
            409, "게시된 공개 항목만 회사 전체에 공개할 수 있습니다"
        ),
        "access_request_closed": ErrorSpec(409, "이미 처리된 접근 요청입니다"),
        "authz_reconcile_running": ErrorSpec(409, "권한 대조가 이미 실행 중입니다"),
        "authz_sync_disabled": ErrorSpec(409, "권한 동기화가 꺼져 있어 실행할 수 없습니다"),
    }
)


def authorization_error(code: ErrorCode, details: Mapping[str, object] | None = None) -> AppError:
    spec = ERRORS[code]
    return AppError(code=code, message=spec.message, status=spec.status, details=details)


def agent_not_ready(
    items: Iterable[Mapping[str, object]],
    *,
    identity_mode: str | None = None,
    agent_id: str | None = None,
) -> AppError:
    """``409 agent_not_ready`` (12.2 ``blocked``): the one body of every refusal (G-02's
    12.3 readiness body, the F-03/F-06 run-prep checks). Items never carry ids a runner
    may not see; callers pass them already shaped."""
    details: dict[str, object] = {"overall": "blocked"}
    if identity_mode is not None:
        details["identity_mode"] = identity_mode
    if agent_id is not None:  # G-04: only when the caller may view the agent's settings
        details["agent_id"] = agent_id
    shown = [dict(item) for item in items]
    details["items"] = shown
    # 14.3 ``readiness_blocked_runs`` (H-04): one line per refused run, counts only.
    logger.info("readiness_blocked_runs", extra={"items": len(shown)})
    return authorization_error("agent_not_ready", details)


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
