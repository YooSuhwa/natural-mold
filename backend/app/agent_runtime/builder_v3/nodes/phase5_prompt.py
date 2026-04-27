"""Phase 5 — 시스템 프롬프트 작성 (generate + approval 2-노드 패턴)."""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from app.agent_runtime.builder.sub_agents.prompt_generator import generate_system_prompt
from app.agent_runtime.builder_v3.constants import ToolNames
from app.agent_runtime.builder_v3.nodes._helpers import (
    build_phase_complete,
    close_pending_tool_card,
    ensure_todos,
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


async def phase5_generate_prompt(state: BuilderState) -> dict:
    intent_dict = state.get("intent") or {}
    tools_data = state.get("tools") or []
    middlewares_data = state.get("middlewares") or []
    revision = state.get("last_revision_message")

    if not intent_dict:
        return {
            "current_phase": 5,
            "error_message": "Phase 5 진입 전 intent가 비어 있습니다.",
        }

    intent_obj = AgentCreationIntent(**intent_dict)
    tools_objs = [ToolRecommendation(**t) for t in tools_data]
    mw_objs = [MiddlewareRecommendation(**m) for m in middlewares_data]

    if revision:
        merged = AgentCreationIntent(**intent_dict)
        merged.agent_description = (merged.agent_description or "") + f"\n\n[수정 요청] {revision}"
        intent_obj = merged

    try:
        prompt = await generate_system_prompt(intent_obj, tools_objs, mw_objs)
    except Exception:  # pragma: no cover
        logger.exception("Prompt generation failed")
        prompt = ""

    msgs, tool_call_id = make_pending_tool_card(
        ToolNames.PROMPT_APPROVAL,
        {
            "phase": 5,
            "title": "시스템 프롬프트",
            "system_prompt": prompt,
            "summary": (
                "에이전트의 시스템 프롬프트를 작성했습니다. "
                "검토 후 승인 또는 수정 요청해주세요."
            ),
        },
        intro_text="이제 시스템 프롬프트를 작성합니다.",
    )

    return {
        "messages": msgs,
        "system_prompt": prompt,
        "last_revision_message": None,
        "current_phase": 5,
        "pending_tool_call_id": tool_call_id,
    }


async def phase5_approval(state: BuilderState) -> dict:
    response = interrupt(
        {
            "type": "approval",
            "phase": 5,
            "title": "시스템 프롬프트 승인",
        }
    )

    approved, revision = parse_approval_response(response)
    pending_tc_id = state.get("pending_tool_call_id")

    if approved:
        close_msgs = close_pending_tool_card(
            pending_tc_id, ToolNames.PROMPT_APPROVAL, "승인됨"
        )
        complete_msgs = build_phase_complete(
            5,
            ensure_todos(state),
            "[Phase 5 완료] 시스템 프롬프트 승인됨. "
            "이제 Phase 6: 에이전트 이미지 생성을 시작하겠습니다.",
        )
        return {
            "messages": [*close_msgs, *complete_msgs],
            "current_phase": 6,
            "last_revision_message": None,
            "pending_tool_call_id": None,
        }

    revision_text = revision or "프롬프트를 다시 작성해주세요"
    close_msgs = close_pending_tool_card(
        pending_tc_id, ToolNames.PROMPT_APPROVAL, f"수정 요청: {revision_text}"
    )
    return {
        "messages": close_msgs,
        "last_revision_message": revision_text,
        "system_prompt": None,  # clear so generate re-runs
        "pending_tool_call_id": None,
    }
