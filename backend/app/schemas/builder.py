"""Builder v2 schemas — 빌드 세션, AgentCreationIntent, 서브에이전트 입출력."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# BuilderStatus StrEnum
# ---------------------------------------------------------------------------


class BuilderStatus(enum.StrEnum):
    """빌드 세션 상태."""

    BUILDING = "building"
    STREAMING = "streaming"
    PREVIEW = "preview"
    CONFIRMING = "confirming"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Builder 요청/응답
# ---------------------------------------------------------------------------


class BuilderStartRequest(BaseModel):
    """POST /api/builder/start — 빌드 세션 시작 요청."""

    user_request: str = Field(..., min_length=1, max_length=2000)


class BuilderSessionResponse(BaseModel):
    """빌드 세션 상태 응답."""

    id: uuid.UUID
    status: BuilderStatus
    current_phase: int = 0
    user_request: str
    intent: AgentCreationIntent | None = None
    tools_result: list[ToolRecommendation] | None = None
    middlewares_result: list[MiddlewareRecommendation] | None = None
    system_prompt: str | None = None
    draft_config: DraftAgentConfig | None = None
    agent_id: uuid.UUID | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# AgentCreationIntent (Phase 2 출력)
# ---------------------------------------------------------------------------


class AgentCreationIntent(BaseModel):
    """의도 분석 서브에이전트의 구조화된 출력."""

    agent_name: str = Field(..., description="영문 에이전트 이름")
    agent_name_ko: str = Field(..., description="한글 에이전트 이름")
    agent_description: str = Field(
        ..., description="에이전트의 역할과 기능에 대한 상세 설명 (3~5문장)"
    )
    primary_task_type: str = Field(..., description="에이전트의 핵심 작업 한 문장")
    tool_preferences: str = Field(default="", description="선호하는 도구 유형")
    output_style: str = Field(default="간단한 요약과 주요 포인트", description="결과물 형태")
    response_tone: str = Field(default="친근하고 캐주얼한 어조", description="응답 톤")
    use_cases: list[str] = Field(
        default_factory=list, min_length=1, description="사용 사례 (최소 1개)"
    )
    constraints: list[str] = Field(default_factory=list, description="제약 조건")
    required_capabilities: list[str] = Field(default_factory=list, description="필수 기능")


# ---------------------------------------------------------------------------
# Tool Recommendation (Phase 3 출력)
# ---------------------------------------------------------------------------


class ToolRecommendation(BaseModel):
    """도구 추천 서브에이전트의 개별 추천 항목."""

    tool_name: str
    description: str
    reason: str


# ---------------------------------------------------------------------------
# Middleware Recommendation (Phase 4 출력)
# ---------------------------------------------------------------------------


class MiddlewareRecommendation(BaseModel):
    """미들웨어 추천 서브에이전트의 개별 추천 항목."""

    middleware_name: str
    description: str
    reason: str


# ---------------------------------------------------------------------------
# DraftAgentConfig (Phase 6-7 출력, confirm 입력)
# ---------------------------------------------------------------------------


class DraftAgentConfig(BaseModel):
    """빌드 파이프라인 최종 산출물 — 사용자 확인용 에이전트 설정 프리뷰."""

    name: str
    name_ko: str
    description: str
    system_prompt: str
    tools: list[str] = Field(default_factory=list, description="도구 이름 목록")
    middlewares: list[str] = Field(default_factory=list, description="미들웨어 이름 목록")
    model_name: str = Field(default="")
    primary_task_type: str = ""
    use_cases: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Builder SSE 이벤트 데이터
# ---------------------------------------------------------------------------


class PhaseProgressEvent(BaseModel):
    """SSE event: phase_progress."""

    phase: int
    status: Literal["started", "completed", "failed", "warning"]
    message: str = ""


class SubAgentEvent(BaseModel):
    """SSE event: sub_agent_start / sub_agent_end."""

    phase: int
    agent_name: str
    result_summary: str = ""


class BuildPreviewEvent(BaseModel):
    """SSE event: build_preview."""

    draft_config: DraftAgentConfig


class BuildErrorEvent(BaseModel):
    """SSE event: error."""

    phase: int
    message: str
    recoverable: bool = False


# ---------------------------------------------------------------------------
# BuilderState (LangGraph 내부 상태 — TypedDict로 실제 사용, 여기는 문서용)
# ---------------------------------------------------------------------------


class BuilderStateSchema(BaseModel):
    """LangGraph BuilderState의 Pydantic 미러 (문서/검증용).

    실제 LangGraph에서는 builder/state.py의 TypedDict를 사용한다.
    이 스키마는 API 테스트와 문서화 목적으로만 사용.
    """

    user_id: str
    user_request: str
    session_id: str = ""
    project_path: str = ""
    intent: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    middlewares: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str = ""
    draft_config: dict[str, Any] | None = None
    agent_id: str = ""
    current_phase: int = 0
    error: str = ""
    available_tools_catalog: list[dict[str, Any]] = Field(default_factory=list)
    available_middlewares_catalog: list[dict[str, Any]] = Field(default_factory=list)
    default_model_name: str = ""


# Pydantic v2 모델 재구성 (forward references 해결)
BuilderSessionResponse.model_rebuild()
