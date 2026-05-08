"""``app.agent_runtime.builder_v3.nodes._helpers.build_approval_result`` 회귀 가드.

Phase 3/4 자체 self-loop 동안 직전 추천 (tools/middlewares) 이 LLM 추천기
입력으로 다시 사용되어야 한다 — 사용자 한정 표현 ("이것만") 정확 반영.
이전 구현은 list 필드를 ``[]`` 로 clear 해 LLM 이 fresh-reasoning 하던
회귀.
"""

from __future__ import annotations

from app.agent_runtime.builder_v3.nodes._helpers import build_approval_result


def _state_with_tools() -> dict:
    return {"tools": [{"tool_name": "x", "kind": "skill"}]}


def test_revision_preserves_tools_list():
    """수정 요청 시 ``tools`` 필드는 결과 dict 에 포함되지 않음 — 기존 state 보존."""
    result = build_approval_result(
        state=_state_with_tools(),  # type: ignore[arg-type]
        approved=False,
        revision="이것만 있으면 될 것 같아",
        pending_tc_id="tc-1",
        tool_name="recommendation_approval",
        phase_id=3,
        next_phase=4,
        completion_message="ok",
        revision_default="다시",
        clear_field="tools",
    )
    # tools 키가 결과에 없어야 — LangGraph TypedDict 미포함 = 기존 state 유지
    assert "tools" not in result
    assert result["last_revision_message"] == "이것만 있으면 될 것 같아"


def test_revision_preserves_middlewares_list():
    """phase4 middlewares 도 동일 보존 (한정 표현 처리 일관성)."""
    result = build_approval_result(
        state={"middlewares": [{"middleware_name": "y"}]},  # type: ignore[arg-type]
        approved=False,
        revision="A 빼고",
        pending_tc_id="tc-2",
        tool_name="recommendation_approval",
        phase_id=4,
        next_phase=5,
        completion_message="ok",
        revision_default="다시",
        clear_field="middlewares",
    )
    assert "middlewares" not in result
    assert result["last_revision_message"] == "A 빼고"


def test_revision_clears_text_field():
    """텍스트 필드 (system_prompt) 는 강제 재생성을 위해 ``None`` 으로 clear."""
    result = build_approval_result(
        state={"system_prompt": "old prompt"},  # type: ignore[arg-type]
        approved=False,
        revision="다시 써줘",
        pending_tc_id="tc-3",
        tool_name="prompt_approval",
        phase_id=5,
        next_phase=6,
        completion_message="ok",
        revision_default="다시",
        clear_field="system_prompt",
    )
    assert result["system_prompt"] is None


def test_approval_advances_phase():
    """승인 시 next_phase 로 전진 + revision message 비움."""
    result = build_approval_result(
        state={},  # type: ignore[arg-type]
        approved=True,
        revision="",
        pending_tc_id="tc-4",
        tool_name="recommendation_approval",
        phase_id=3,
        next_phase=4,
        completion_message="next",
        revision_default="다시",
        clear_field="tools",
    )
    assert result["current_phase"] == 4
    assert result["last_revision_message"] is None
    assert "tools" not in result  # 승인 시도 list 필드 보존
