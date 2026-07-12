"""Centralized error factory functions.

Exception 인스턴스는 생성 시점에 traceback을 캡처하므로, 상수가 아닌 팩토리 함수로 제공한다.
"""

from app.exceptions import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# NotFoundError (404)
# ---------------------------------------------------------------------------


def agent_not_found() -> NotFoundError:
    return NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")


def conversation_not_found() -> NotFoundError:
    return NotFoundError("CONVERSATION_NOT_FOUND", "대화를 찾을 수 없습니다")


def provider_not_found() -> NotFoundError:
    return NotFoundError("PROVIDER_NOT_FOUND", "프로바이더를 찾을 수 없습니다")


def tool_not_found() -> NotFoundError:
    return NotFoundError("TOOL_NOT_FOUND", "도구를 찾을 수 없습니다")


def session_not_found() -> NotFoundError:
    return NotFoundError("SESSION_NOT_FOUND", "빌드 세션을 찾을 수 없습니다")


def trigger_not_found() -> NotFoundError:
    return NotFoundError("TRIGGER_NOT_FOUND", "트리거를 찾을 수 없습니다")


def model_not_found() -> NotFoundError:
    return NotFoundError("MODEL_NOT_FOUND", "모델을 찾을 수 없습니다")


def template_not_found() -> NotFoundError:
    return NotFoundError("TEMPLATE_NOT_FOUND", "템플릿을 찾을 수 없습니다")


def image_not_found() -> NotFoundError:
    return NotFoundError("IMAGE_NOT_FOUND", "이미지를 찾을 수 없습니다")


def image_file_not_found() -> NotFoundError:
    return NotFoundError("IMAGE_NOT_FOUND", "이미지 파일을 찾을 수 없습니다")


def file_not_found() -> NotFoundError:
    return NotFoundError("FILE_NOT_FOUND", "파일을 찾을 수 없습니다")


def skill_not_found() -> NotFoundError:
    return NotFoundError("SKILL_NOT_FOUND", "스킬을 찾을 수 없습니다")


def skill_file_not_found() -> NotFoundError:
    return NotFoundError("SKILL_FILE_NOT_FOUND", "스킬 파일을 찾을 수 없습니다")


def skill_revision_not_found() -> NotFoundError:
    return NotFoundError("SKILL_REVISION_NOT_FOUND", "스킬 이력을 찾을 수 없습니다")


def skill_revision_snapshot_unavailable() -> ConflictError:
    return ConflictError(
        "SKILL_REVISION_SNAPSHOT_UNAVAILABLE",
        "이 리비전의 스냅샷이 정리되어 되돌릴 수 없습니다",
    )


def skill_evaluation_set_not_found() -> NotFoundError:
    return NotFoundError("SKILL_EVALUATION_SET_NOT_FOUND", "스킬 평가 세트를 찾을 수 없습니다")


def skill_evaluation_run_not_found() -> NotFoundError:
    return NotFoundError("SKILL_EVALUATION_RUN_NOT_FOUND", "스킬 평가 실행을 찾을 수 없습니다")


def marketplace_item_not_found() -> NotFoundError:
    """Spec §10.7 — emitted for both "doesn't exist" and "forbidden" so
    catalog enumeration via 404 vs 403 is blocked (rules/security.md).
    The branch is recorded server-side only."""
    return NotFoundError("MARKETPLACE_ITEM_NOT_FOUND", "마켓플레이스 아이템을 찾을 수 없습니다")


def marketplace_version_not_found() -> NotFoundError:
    return NotFoundError("MARKETPLACE_VERSION_NOT_FOUND", "마켓플레이스 버전을 찾을 수 없습니다")


def credential_not_found() -> NotFoundError:
    return NotFoundError("CREDENTIAL_NOT_FOUND", "크리덴셜을 찾을 수 없습니다")


def share_not_found() -> NotFoundError:
    return NotFoundError("SHARE_NOT_FOUND", "공유 링크를 찾을 수 없습니다")


def memory_not_found() -> NotFoundError:
    return NotFoundError("MEMORY_NOT_FOUND", "메모리를 찾을 수 없습니다")


def memory_proposal_not_found() -> NotFoundError:
    return NotFoundError(
        "MEMORY_PROPOSAL_NOT_FOUND",
        "메모리 제안을 찾을 수 없습니다",
    )


def resume_not_found() -> NotFoundError:
    return NotFoundError("RESUME_NOT_FOUND", "재개할 스트림을 찾을 수 없습니다")


def trace_not_found() -> NotFoundError:
    return NotFoundError("TRACE_NOT_FOUND", "트레이스를 찾을 수 없습니다")


def mcp_server_not_found() -> NotFoundError:
    return NotFoundError("MCP_SERVER_NOT_FOUND", "MCP 서버를 찾을 수 없습니다")


def system_credential_not_found() -> NotFoundError:
    return NotFoundError("SYSTEM_CREDENTIAL_NOT_FOUND", "시스템 자격증명을 찾을 수 없습니다")


def unknown_credential_definition(key: str) -> NotFoundError:
    return NotFoundError("UNKNOWN_CREDENTIAL_DEFINITION", f"알 수 없는 자격증명 유형입니다: {key}")


def unknown_tool_definition(key: str) -> NotFoundError:
    return NotFoundError("UNKNOWN_TOOL_DEFINITION", f"알 수 없는 도구 유형입니다: {key}")


def unknown_registry_entry(key: str) -> NotFoundError:
    return NotFoundError("UNKNOWN_REGISTRY_ENTRY", f"알 수 없는 레지스트리 항목입니다: {key}")


# ---------------------------------------------------------------------------
# ValidationError (422)
# ---------------------------------------------------------------------------


def invalid_trigger_type() -> ValidationError:
    return ValidationError(
        "INVALID_TRIGGER_TYPE",
        "trigger_type은 'interval', 'cron', 'one_time' 중 하나여야 합니다",
    )


def invalid_schedule_config() -> ValidationError:
    return ValidationError(
        "INVALID_SCHEDULE_CONFIG",
        "스케줄 설정이 올바르지 않습니다",
    )


def agent_identity_requires_fixed() -> ValidationError:
    return ValidationError(
        "AGENT_IDENTITY_REQUIRES_FIXED",
        "자동 실행에는 에이전트 고정 credential 사용(fixed)이 필요합니다",
    )


def session_not_preview() -> ValidationError:
    return ValidationError("SESSION_NOT_PREVIEW", "프리뷰 상태의 세션만 확인할 수 있습니다")


def no_draft_config() -> ValidationError:
    return ValidationError("NO_DRAFT_CONFIG", "드래프트 설정이 없습니다")


def invalid_file_path() -> ValidationError:
    return ValidationError("INVALID_FILE_PATH", "잘못된 파일 경로입니다")


def invalid_skill_package(detail: str) -> ValidationError:
    return ValidationError("INVALID_SKILL_PACKAGE", detail)


def marketplace_credential_mismatch(detail: str) -> ValidationError:
    """Spec §10.7 — credential definition_key / requirement_key mismatch
    on a binding write. 400 ``ValidationError`` keeps client-side hints
    actionable (the requirement / definition info is non-sensitive)."""
    return ValidationError("MARKETPLACE_CREDENTIAL_MISMATCH", detail)


def marketplace_invalid_package(detail: str) -> ValidationError:
    """Spec §10.7 — version payload / storage snapshot is unreadable or
    malformed (missing storage_path, missing SKILL.md, copy failure).
    400 because the user can re-publish or pick a different version."""
    return ValidationError("MARKETPLACE_INVALID_PACKAGE", detail)


def marketplace_secret_detected(detail: str) -> ValidationError:
    """Spec §13.1 — secret_scan rejected the package on publish or
    import. 400 with the finding list folded into ``detail`` so the
    operator can remediate without a second round-trip."""
    return ValidationError("MARKETPLACE_SECRET_DETECTED", detail)


def marketplace_invalid_visibility(detail: str) -> ValidationError:
    """Spec §10.7 — invalid visibility transition (e.g. trying to
    publish as ``system`` from a user route)."""
    return ValidationError("MARKETPLACE_INVALID_VISIBILITY", detail)


def marketplace_acl_required() -> ValidationError:
    """Spec §10.7 — ``visibility='restricted'`` requires at least one
    user_id in the ACL. 400 because the user can retry with valid ACL."""
    return ValidationError(
        "MARKETPLACE_ACL_REQUIRED",
        "restricted 가시성은 최소 1명의 ACL 사용자가 필요합니다",
    )


def marketplace_manage_forbidden() -> ForbiddenError:
    """Spec §10.7 — user is authenticated and *can see* the item, but
    is not its owner / super_user. 403 rather than 404 because the
    existence is already visible (item appeared in the catalog)."""
    return ForbiddenError("MARKETPLACE_MANAGE_FORBIDDEN", "관리 권한이 없습니다")


def super_user_required() -> ForbiddenError:
    return ForbiddenError("SUPER_USER_REQUIRED", "운영자 권한이 필요합니다")


def credential_forbidden() -> ForbiddenError:
    return ForbiddenError("FORBIDDEN", "권한이 없습니다")


# ---------------------------------------------------------------------------
# ConflictError (409)
# ---------------------------------------------------------------------------


def session_already_claimed() -> ConflictError:
    return ConflictError(
        "SESSION_ALREADY_CLAIMED",
        "이미 스트리밍 중이거나 빌드 중 상태가 아닙니다",
    )


def trigger_already_running() -> ConflictError:
    return ConflictError(
        "TRIGGER_ALREADY_RUNNING",
        "이미 실행 중인 스케줄입니다. 실행이 끝난 뒤 다시 시도하세요",
    )


def session_confirming() -> ConflictError:
    return ConflictError(
        "SESSION_CONFIRMING",
        "에이전트 생성이 이미 진행 중입니다",
    )


def resume_interrupt_pending() -> ConflictError:
    return ConflictError(
        "RESUME_INTERRUPT_PENDING",
        "HiTL 인터럽트 응답 대기 중입니다 — /messages/resume 으로 재개하세요",
    )


def marketplace_credential_required(detail: str) -> ConflictError:
    """Spec §10.7 — install/runtime requires a credential that is not yet
    bound. 409 because the install/run flow can be retried after the
    user creates + binds the credential (idempotent)."""
    return ConflictError("MARKETPLACE_CREDENTIAL_REQUIRED", detail)


def system_llm_not_configured() -> ConflictError:
    return ConflictError(
        "SYSTEM_LLM_NOT_CONFIGURED",
        "시스템 LLM 설정이 필요합니다",
    )


def skill_builder_source_conflict() -> ConflictError:
    return ConflictError(
        "SKILL_BUILDER_SOURCE_CONFLICT",
        "개선 세션 시작 이후 스킬이 변경되었습니다",
    )


def skill_builder_session_not_ready() -> ConflictError:
    return ConflictError(
        "SKILL_BUILDER_SESSION_NOT_READY",
        "검토 가능한 스킬 초안이 필요합니다",
    )


def skill_evaluation_run_not_cancellable() -> ConflictError:
    return ConflictError(
        "SKILL_EVALUATION_RUN_NOT_CANCELLABLE",
        "취소할 수 없는 평가 실행 상태입니다",
    )


def skill_evaluation_queue_full() -> ConflictError:
    return ConflictError(
        "SKILL_EVALUATION_QUEUE_FULL",
        "스킬 평가 실행 대기열이 가득 찼습니다",
    )


def marketplace_dirty_installation() -> ConflictError:
    """Spec §10.3 — update requires an explicit ``strategy`` when the
    installation is dirty (the user has edited the installed copy).
    The client must pick overwrite / install_new_copy / keep_current."""
    return ConflictError(
        "MARKETPLACE_DIRTY_INSTALLATION",
        "수정된 설치본은 업데이트 전략을 지정해야 합니다",
    )


# ---------------------------------------------------------------------------
# ForbiddenError (403)
# ---------------------------------------------------------------------------


def resume_forbidden() -> ForbiddenError:
    """Reserved — 현재 의도적 미사용.

    W3-out M3 시점에 stream resume 의 모든 권한/존재 거부 분기는
    ``RESUME_NOT_FOUND`` 단일 응답으로 통일됐다 (rules/security.md —
    enumeration oracle 방지). 향후 명시적 share/public link 등이 도입되어
    "리소스가 공개적으로 알려졌고 권한만 부족하다" 분기가 생기면 이 helper 가
    재사용 후보. 그때까지는 declared-but-unused.
    """
    return ForbiddenError("RESUME_FORBIDDEN", "이 대화의 스트림에 접근할 수 없습니다")


# ---------------------------------------------------------------------------
# AppError (generic / 500)
# ---------------------------------------------------------------------------


def agent_creation_failed(
    detail: str = "에이전트 생성 중 오류가 발생했습니다. 다시 시도해주세요.",
) -> AppError:
    return AppError("AGENT_CREATION_FAILED", detail, status=500)
