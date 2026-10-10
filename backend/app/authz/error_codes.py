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
        "resource_transfer_required": ErrorSpec(
            409, "소유한 자원을 먼저 이전하거나 삭제해야 합니다"
        ),
    }
)


def authorization_error(code: ErrorCode) -> AppError:
    spec = ERRORS[code]
    return AppError(code=code, message=spec.message, status=spec.status)
