"""Phase 1 — 프로젝트 초기화 (LLM 불필요).

진입 메시지 + 진행 상황 카드 emit + 다음 phase로 진입.
"""

from __future__ import annotations

from app.agent_runtime.builder_v3.nodes._helpers import (
    build_phase_complete,
    get_last_user_text,
    make_tool_card,
)
from app.agent_runtime.builder_v3.state import BuilderState, initial_todos
from app.agent_runtime.builder_v3.todos import (
    PHASE_TIMELINE_TOOL,
    mark_completed_through,
    update_phase_status,
)


async def phase1_init(state: BuilderState) -> dict:
    """첫 진입: 환영 메시지 + 8-phase 진행 상황 카드 emit."""
    user_request = state.get("user_request") or get_last_user_text(state)

    todos = state.get("todos") or initial_todos()

    # 진입: Phase 1 in_progress
    in_progress_todos = update_phase_status(todos, 1, "in_progress")
    intro_msgs, _ = make_tool_card(
        PHASE_TIMELINE_TOOL,
        {"todos": [dict(t) for t in in_progress_todos]},
        intro_text=(
            "에이전트를 만들어드리겠습니다! 먼저 작업 목록을 설정하고 단계별로 진행하겠습니다.\n\n"
            "이제 Phase 1: 프로젝트 초기화를 진행하겠습니다."
        ),
    )

    # 작업 — 단순한 path 문자열 (실제 파일 생성 없음, 메타용)
    project_path = f"agent_builds/{state.get('session_id', 'session')}"

    # 완료 메시지 + 카드 갱신
    complete_msgs = build_phase_complete(
        1,
        in_progress_todos,
        "[Phase 1 완료] 프로젝트 초기화 완료. 이제 Phase 2: 사용자 의도 분석을 시작합니다.",
    )
    final_todos = mark_completed_through(in_progress_todos, 1)

    return {
        "messages": intro_msgs + complete_msgs,
        "todos": final_todos,
        "user_request": user_request,
        "project_path": project_path,
        "current_phase": 2,
    }
