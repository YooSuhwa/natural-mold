"""Builder v3 — BuilderState TypedDict + Phase 정의.

LangGraph StateGraph가 사용하는 상태. messages는 add_messages reducer로 누적,
나머지는 단순 덮어쓰기 (default reducer).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

PhaseStatus = Literal["pending", "in_progress", "completed"]
PhaseId = Literal[1, 2, 3, 4, 5, 6, 7, 8]


PHASE_DEFINITIONS: list[dict[str, Any]] = [
    {"id": 1, "name": "프로젝트 초기화", "label_en": "Project Initialization"},
    {"id": 2, "name": "사용자 의도 분석", "label_en": "Intent Analysis"},
    {"id": 3, "name": "도구 추천", "label_en": "Tool Recommendation"},
    {"id": 4, "name": "미들웨어 추천", "label_en": "Middleware Recommendation"},
    {"id": 5, "name": "시스템 프롬프트 작성", "label_en": "System Prompt"},
    {"id": 6, "name": "에이전트 이미지 생성", "label_en": "Agent Image"},
    {"id": 7, "name": "에이전트 설정 저장", "label_en": "Save Configuration"},
    {"id": 8, "name": "에이전트 빌드", "label_en": "Build Agent"},
]


class PhaseTodo(TypedDict):
    """Phase 진행 상황 카드의 단일 항목."""

    id: int
    name: str
    status: PhaseStatus


class BuilderState(TypedDict, total=False):
    """LangGraph StateGraph가 관리하는 빌더 세션 상태.

    `total=False`로 모든 키를 optional로 두어, 노드가 부분 업데이트만 반환해도 됨.
    """

    # 메시지 히스토리 (assistant-ui 호환)
    messages: Annotated[list[BaseMessage], add_messages]

    # 진행 상황 카드 (8-phase)
    todos: list[PhaseTodo]

    # 첫 사용자 요청 (Phase 1에서 messages에서 추출하여 저장)
    user_request: str

    # 카탈로그/메타 (Phase 1에서 주입)
    user_id: str
    session_id: str
    tools_catalog: list[dict[str, Any]]
    middlewares_catalog: list[dict[str, Any]]
    default_model_name: str
    project_path: str

    # Phase별 결과
    intent: dict[str, Any] | None             # Phase 2
    tools: list[dict[str, Any]]               # Phase 3 (ToolRecommendation list)
    middlewares: list[dict[str, Any]]         # Phase 4
    system_prompt: str | None                 # Phase 5
    image_url: str | None                     # Phase 6 (None이면 이미지 없음)
    draft_config: dict[str, Any] | None       # Phase 7

    # 진행 위치
    current_phase: int

    # phase 3/4/5 승인 루프에서 LLM에 전달할 수정 의견
    last_revision_message: str | None

    # phase 2가 사용자 이름 확인을 받았는지 (재진입 시 ask_user 스킵 여부)
    intent_confirmed: bool

    # phase 6 분기 신호 (skip/confirm 시 True → graph가 phase7로 라우팅)
    image_skipped: bool

    # 직전 propose 노드가 emit한 pending tool_call의 id (wait 노드가 ToolMessage close용)
    pending_tool_call_id: str | None

    # phase 8에서 router가 분기를 결정한 phase (디버깅/감사용)
    last_router_decision: str | None

    # 종료 신호
    completed: bool
    agent_id: str | None
    error_message: str | None


def initial_todos() -> list[PhaseTodo]:
    """8개 phase 초기 상태 (모두 pending)."""
    return [
        {"id": p["id"], "name": p["name"], "status": "pending"} for p in PHASE_DEFINITIONS
    ]


def get_phase_name(phase_id: int) -> str:
    for p in PHASE_DEFINITIONS:
        if p["id"] == phase_id:
            return str(p["name"])
    return f"Phase {phase_id}"
