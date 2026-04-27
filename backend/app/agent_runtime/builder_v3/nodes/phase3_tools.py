"""Phase 3 — 도구 추천 (generate + approval 2-노드 패턴).

phase3_recommend_tools: tool_recommender 호출 → ToolMessage(recommendation_approval) emit
phase3_approval: interrupt(approval) → 승인이면 Phase 4, 수정이면 self-loop으로 재추천
"""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from app.agent_runtime.builder.sub_agents.tool_recommender import recommend_tools
from app.agent_runtime.builder_v3.constants import ToolNames
from app.agent_runtime.builder_v3.nodes._helpers import (
    build_approval_result,
    make_pending_tool_card,
    parse_approval_response,
)
from app.agent_runtime.builder_v3.state import BuilderState
from app.schemas.builder import AgentCreationIntent, ToolRecommendation

logger = logging.getLogger(__name__)


async def phase3_recommend_tools(state: BuilderState) -> dict:
    """도구 추천 LLM 호출 → state.tools 갱신 + RecommendationApprovalCard 카드 emit."""
    intent_dict = state.get("intent") or {}
    catalog = state.get("tools_catalog") or []
    revision = state.get("last_revision_message")

    intent_obj = AgentCreationIntent(**intent_dict) if intent_dict else None

    if not intent_obj:
        return {
            "current_phase": 3,
            "error_message": "Phase 3 진입 전 intent가 비어 있습니다.",
        }

    # revision 메시지가 있으면 description에 prepend
    if revision:
        # tool_recommender는 prompt를 task description에 추가하는 방식이 깔끔하지 않으므로,
        # 임시로 intent.tool_preferences에 합치는 방식 사용
        merged = AgentCreationIntent(**intent_dict)
        suffix = f"\n\n[사용자 수정 요청] {revision}"
        merged.tool_preferences = (merged.tool_preferences or "") + suffix
        intent_obj = merged

    try:
        tool_objs: list[ToolRecommendation] = await recommend_tools(intent_obj, catalog)
    except Exception:  # pragma: no cover
        logger.exception("Tool recommendation failed")
        tool_objs = []

    tools_data = [t.model_dump(mode="json") for t in tool_objs]
    summary_text = (
        f"{len(tools_data)}개의 도구를 추천합니다. 검토 후 승인 또는 수정 요청해주세요."
        if tools_data
        else "추천된 도구가 없습니다. 수정 의견을 입력해주세요."
    )

    msgs, tool_call_id = make_pending_tool_card(
        ToolNames.RECOMMENDATION_APPROVAL,
        {
            "phase": 3,
            "title": "도구 추천",
            "items": tools_data,
            "summary": summary_text,
            "item_kind": "tool",
        },
        intro_text="이제 도구를 추천받겠습니다.",
    )

    return {
        "messages": msgs,
        "tools": tools_data,
        "last_revision_message": None,
        "current_phase": 3,
        "pending_tool_call_id": tool_call_id,
    }


async def phase3_approval(state: BuilderState) -> dict:
    """interrupt(approval). 승인/수정 응답을 state에 반영. 라우팅은 graph가."""
    response = interrupt(
        {
            "type": "approval",
            "phase": 3,
            "title": "도구 추천 승인",
        }
    )

    approved, revision = parse_approval_response(response)
    return build_approval_result(
        state=state,
        approved=approved,
        revision=revision,
        pending_tc_id=state.get("pending_tool_call_id"),
        tool_name=ToolNames.RECOMMENDATION_APPROVAL,
        phase_id=3,
        next_phase=4,
        completion_message=(
            "[Phase 3 완료] 도구 추천 승인됨. "
            "이제 Phase 4: 미들웨어 추천을 시작하겠습니다."
        ),
        revision_default="다른 도구를 추천해주세요",
        clear_field="tools",
    )
