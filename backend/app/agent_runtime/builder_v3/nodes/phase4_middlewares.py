"""Phase 4 — 미들웨어 추천 (generate + approval 2-노드 패턴)."""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from app.agent_runtime.builder.sub_agents.middleware_recommender import (
    recommend_middlewares,
)
from app.agent_runtime.builder_v3.constants import ToolNames
from app.agent_runtime.builder_v3.nodes._helpers import (
    build_approval_result,
    make_pending_tool_card,
    parse_approval_response,
)
from app.agent_runtime.builder_v3.state import BuilderState
from app.schemas.builder import (
    AgentCreationIntent,
    MiddlewareRecommendation,
    ToolRecommendation,
)

logger = logging.getLogger(__name__)


async def phase4_recommend_middlewares(state: BuilderState) -> dict:
    intent_dict = state.get("intent") or {}
    catalog = state.get("middlewares_catalog") or []
    tools_data = state.get("tools") or []
    revision = state.get("last_revision_message")

    if not intent_dict:
        return {
            "current_phase": 4,
            "error_message": "Phase 4 진입 전 intent가 비어 있습니다.",
        }

    intent_obj = AgentCreationIntent(**intent_dict)
    tools_objs = [ToolRecommendation(**t) for t in tools_data]

    if revision:
        merged = AgentCreationIntent(**intent_dict)
        merged.constraints = list(merged.constraints) + [f"[수정 요청] {revision}"]
        intent_obj = merged

    try:
        mw_objs: list[MiddlewareRecommendation] = await recommend_middlewares(
            intent_obj, tools_objs, catalog
        )
    except Exception:  # pragma: no cover
        logger.exception("Middleware recommendation failed")
        mw_objs = []

    mw_data = [m.model_dump(mode="json") for m in mw_objs]
    summary_text = (
        f"{len(mw_data)}개의 미들웨어를 추천합니다. 검토 후 승인 또는 수정 요청해주세요."
        if mw_data
        else "추천된 미들웨어가 없습니다. 그대로 승인하거나 수정 의견을 주세요."
    )

    msgs, tool_call_id = make_pending_tool_card(
        ToolNames.RECOMMENDATION_APPROVAL,
        {
            "phase": 4,
            "title": "미들웨어 추천",
            "items": mw_data,
            "summary": summary_text,
            "item_kind": "middleware",
        },
        intro_text="이제 미들웨어를 추천받겠습니다.",
    )

    return {
        "messages": msgs,
        "middlewares": mw_data,
        "last_revision_message": None,
        "current_phase": 4,
        "pending_tool_call_id": tool_call_id,
    }


async def phase4_approval(state: BuilderState) -> dict:
    response = interrupt(
        {
            "type": "approval",
            "phase": 4,
            "title": "미들웨어 추천 승인",
        }
    )

    approved, revision = parse_approval_response(response)
    return build_approval_result(
        state=state,
        approved=approved,
        revision=revision,
        pending_tc_id=state.get("pending_tool_call_id"),
        tool_name=ToolNames.RECOMMENDATION_APPROVAL,
        phase_id=4,
        next_phase=5,
        completion_message=(
            "[Phase 4 완료] 미들웨어 추천 승인됨. "
            "이제 Phase 5: 시스템 프롬프트 작성을 시작하겠습니다."
        ),
        revision_default="다른 미들웨어를 추천해주세요",
        clear_field="middlewares",
    )
