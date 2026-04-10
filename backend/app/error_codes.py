"""Centralized error factory functions.

Exception 인스턴스는 생성 시점에 traceback을 캡처하므로, 상수가 아닌 팩토리 함수로 제공한다.
"""

from app.exceptions import AppError, ConflictError, NotFoundError, ValidationError

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


def mcp_server_not_found() -> NotFoundError:
    return NotFoundError("MCP_SERVER_NOT_FOUND", "MCP 서버를 찾을 수 없습니다")


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


# ---------------------------------------------------------------------------
# ValidationError (422)
# ---------------------------------------------------------------------------


def invalid_trigger_type() -> ValidationError:
    return ValidationError(
        "INVALID_TRIGGER_TYPE", "trigger_type은 'interval' 또는 'cron'이어야 합니다"
    )


def invalid_schedule_config() -> ValidationError:
    return ValidationError(
        "INVALID_SCHEDULE_CONFIG",
        "interval은 schedule_config.interval_minutes >= 1이 필요합니다",
    )


def session_not_preview() -> ValidationError:
    return ValidationError(
        "SESSION_NOT_PREVIEW", "프리뷰 상태의 세션만 확인할 수 있습니다"
    )


def no_draft_config() -> ValidationError:
    return ValidationError("NO_DRAFT_CONFIG", "드래프트 설정이 없습니다")


def invalid_file_path() -> ValidationError:
    return ValidationError("INVALID_FILE_PATH", "잘못된 파일 경로입니다")


def invalid_skill_package(detail: str) -> ValidationError:
    return ValidationError("INVALID_SKILL_PACKAGE", detail)


# ---------------------------------------------------------------------------
# ConflictError (409)
# ---------------------------------------------------------------------------


def session_already_claimed() -> ConflictError:
    return ConflictError(
        "SESSION_ALREADY_CLAIMED",
        "이미 스트리밍 중이거나 빌드 중 상태가 아닙니다",
    )


def session_confirming() -> ConflictError:
    return ConflictError(
        "SESSION_CONFIRMING",
        "에이전트 생성이 이미 진행 중입니다",
    )


# ---------------------------------------------------------------------------
# AppError (generic / 500)
# ---------------------------------------------------------------------------


def agent_creation_failed(
    detail: str = "에이전트 생성 중 오류가 발생했습니다. 다시 시도해주세요.",
) -> AppError:
    return AppError("AGENT_CREATION_FAILED", detail, status=500)
