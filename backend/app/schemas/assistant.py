"""Assistant v2 schemas — 에이전트 설정 수정 도우미 메시지/이벤트."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Assistant 요청/응답
# ---------------------------------------------------------------------------


class AssistantMessageRequest(BaseModel):
    """POST /api/agents/{agent_id}/assistant/message — 메시지 요청."""

    content: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None,
        description="클라이언트가 생성한 세션 ID (crypto.randomUUID). "
        "같은 session_id는 같은 대화를 유지, 없으면 agent_id 기반 기본값 사용.",
    )


class AssistantMessageResponse(BaseModel):
    """Assistant 메시지 응답 (SSE message_end 이벤트의 최종 데이터)."""

    role: str = "assistant"
    content: str
    tool_calls: list[AssistantToolCallResult] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)


class AssistantToolCallResult(BaseModel):
    """Assistant가 실행한 개별 도구 호출 결과 요약."""

    tool_name: str
    success: bool = True
    summary: str = ""


# ---------------------------------------------------------------------------
# Assistant 도구 입출력 스키마
# ---------------------------------------------------------------------------


class AgentConfigSnapshot(BaseModel):
    """get_agent_config 도구 반환값 — 에이전트 현재 설정 스냅샷."""

    agent_id: uuid.UUID
    name: str
    description: str | None = None
    system_prompt: str
    model_name: str
    model_params: dict[str, Any] | None = None
    tools: list[AgentToolInfo] = Field(default_factory=list)
    middlewares: list[AgentMiddlewareInfo] = Field(default_factory=list)
    skills: list[AgentSkillInfo] = Field(default_factory=list)


class AgentToolInfo(BaseModel):
    """에이전트에 연결된 도구 요약 정보."""

    name: str
    description: str | None = None
    tool_type: str = ""  # builtin, prebuilt, custom, mcp
    config: dict[str, Any] = Field(default_factory=dict)


class AgentMiddlewareInfo(BaseModel):
    """에이전트에 연결된 미들웨어 요약 정보."""

    type: str
    display_name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class AgentSkillInfo(BaseModel):
    """에이전트에 연결된 스킬 요약 정보."""

    name: str
    description: str | None = None


# ---------------------------------------------------------------------------
# 도구 카탈로그 조회 결과
# ---------------------------------------------------------------------------


class AvailableToolItem(BaseModel):
    """list_available_tools 도구 반환값의 개별 항목."""

    name: str
    description: str | None = None
    tool_type: str
    required_secrets: list[str] = Field(default_factory=list)


class AvailableMiddlewareItem(BaseModel):
    """list_available_middlewares 도구 반환값의 개별 항목."""

    name: str
    display_name: str
    description: str
    category: str
    config_schema: dict[str, Any] = Field(default_factory=dict)


class AvailableModelItem(BaseModel):
    """list_available_models 도구 반환값의 개별 항목."""

    id: uuid.UUID
    display_name: str
    provider: str
    model_id: str


# ---------------------------------------------------------------------------
# 리소스 추가/제거 도구 입력
# ---------------------------------------------------------------------------


class AddResourceInput(BaseModel):
    """add_tool_to_agent / add_middleware_to_agent 등의 입력."""

    names: list[str] = Field(..., min_length=1)


class RemoveResourceInput(BaseModel):
    """remove_tool_from_agent / remove_middleware_from_agent 등의 입력."""

    names: list[str] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# 시스템 프롬프트 수정 도구 입력
# ---------------------------------------------------------------------------


class EditSystemPromptInput(BaseModel):
    """edit_system_prompt 도구 입력."""

    old_string: str = Field(..., min_length=1)
    new_string: str  # 빈 문자열 = 삭제
    replace_all: bool = False


class UpdateSystemPromptInput(BaseModel):
    """update_system_prompt 도구 입력."""

    new_system_prompt: str = Field(..., min_length=1)


class SearchSystemPromptInput(BaseModel):
    """search_system_prompt 도구 입력."""

    keyword: str = Field(..., min_length=1)


class SearchSystemPromptResult(BaseModel):
    """search_system_prompt 도구 출력."""

    found: bool
    matches: list[PromptSearchMatch] = Field(default_factory=list)


class PromptSearchMatch(BaseModel):
    """프롬프트 내 키워드 매치 결과."""

    text: str  # 매치된 텍스트 (전후 컨텍스트 포함)
    line_number: int = 0


# ---------------------------------------------------------------------------
# 모델 설정 도구
# ---------------------------------------------------------------------------


class UpdateModelConfigInput(BaseModel):
    """update_model_config 도구 입력."""

    model_name: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    top_k: int | None = None


# ---------------------------------------------------------------------------
# 크론 스케줄 도구
# ---------------------------------------------------------------------------


class CronScheduleInput(BaseModel):
    """create_cron_schedule 도구 입력."""

    schedule_type: str = Field(..., pattern="^(recurring|one_time)$")
    cron_expression: str | None = None  # recurring일 때 필수
    scheduled_at: str | None = None  # one_time일 때 필수 (ISO 8601)
    timezone: str = "Asia/Seoul"
    message: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Clarifying Question 도구
# ---------------------------------------------------------------------------


class AskClarifyingQuestionInput(BaseModel):
    """ask_clarifying_question 도구 입력."""

    question: str
    option_1: str
    option_2: str
    option_3: str


class ClarifyingQuestionOutput(BaseModel):
    """ask_clarifying_question 도구 출력 (프론트엔드에 표시)."""

    question: str
    options: list[str]  # 3개 옵션 + "직접 입력"


# ---------------------------------------------------------------------------
# Secrets 확인 도구
# ---------------------------------------------------------------------------


class RequiredSecretsResult(BaseModel):
    """get_agent_required_secrets 도구 출력."""

    required: list[str]
    registered: list[str]
    missing: list[str]


# Pydantic v2 forward reference 해결
SearchSystemPromptResult.model_rebuild()
