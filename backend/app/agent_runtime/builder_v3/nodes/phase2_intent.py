"""Phase 2 — 사용자 의도 분석 (analyze + wait 2-노드 패턴).

LangGraph `interrupt()`는 ToolMessage를 자동 생성하지 않으므로,
propose(=analyze) 노드에서 `ask_user` ToolMessage를 messages에 emit하고,
wait 노드에서 interrupt를 호출한다. resume 후 응답에 따라 self-loop 또는 Phase 3로.

흐름:
    phase2_analyze_intent
      ├ intent_confirmed=True  → Command(goto="phase3_recommend_tools", ...)
      └ 그 외                  → ToolMessage(ask_user) emit + dict 반환
                                   ↓ (fixed edge)
    phase2_intent_wait → interrupt → resume:
      ├ 빈/직접 입력 → Command(goto="phase2_analyze_intent")
      └ 옵션 선택   → Command(goto="phase2_analyze_intent",
                              update={intent_confirmed=True, intent.agent_name_ko=..})
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from app.agent_runtime.builder.sub_agents.helpers import invoke_with_json_retry
from app.agent_runtime.builder.sub_agents.intent_analyzer import analyze_intent
from app.agent_runtime.builder_v3.nodes._helpers import (
    _extract_text_from_content,
    build_phase_complete,
    close_pending_tool_card,
    ensure_todos,
    get_last_user_text,
    make_pending_tool_card,
)
from app.agent_runtime.builder_v3.state import BuilderState

logger = logging.getLogger(__name__)


# fallback 라벨 (intent_analyzer.py가 파싱 실패 시 채우는 값)
_FALLBACK_NAMES = {"Custom Agent", "맞춤 에이전트", ""}

_ASK_QUESTION = "만들고 싶은 에이전트의 이름을 무엇으로 하시겠습니까?"


def _build_combined_request(state: BuilderState) -> str:
    """초기 user_request + 후속 사용자 응답 + Phase 8 router의 revision 결합.

    Phase 8 → router → phase 2 재진입 시 ``last_revision_message`` 가 set된 상태.
    이 메시지를 prompt에 포함해야 LLM이 사용자 수정 의견을 반영한다 (phase 3/4/5 패턴).
    """
    parts: list[str] = []
    initial = state.get("user_request") or ""
    if initial:
        parts.append(initial)

    seen_initial = False
    for msg in state.get("messages") or []:
        if isinstance(msg, HumanMessage):
            content = _extract_text_from_content(msg.content)
            if not seen_initial and content == initial:
                seen_initial = True
                continue
            if content and content not in parts:
                parts.append(content)

    revision = state.get("last_revision_message")
    if revision:
        parts.append(f"[수정 요청] {revision}")

    return "\n\n".join(parts) if parts else (initial or "")


async def _suggest_name_options(user_request: str) -> list[str]:
    """LLM에게 에이전트 이름 후보 3개를 생성해달라고 한다."""
    system = (
        "당신은 AI 에이전트 네이밍 전문가입니다. "
        "사용자의 요청을 보고 어울리는 에이전트 이름 3개를 한국어로 제시합니다. "
        "JSON 배열 형식으로만 응답하세요. 예: [\"이름1\", \"이름2\", \"이름3\"]"
    )
    task = (
        f"사용자 요청: '{user_request}'\n\n"
        "이 요청에 어울리는 에이전트 이름 후보 3개를 JSON 배열로 제시해주세요. "
        "각 이름은 5~12자, 한국어, 명사구."
    )
    try:
        result = await invoke_with_json_retry(system, task, max_retries=1)
        if isinstance(result, list):
            cleaned = [str(x).strip() for x in result if str(x).strip()]
            if len(cleaned) >= 2:
                return cleaned[:3]
    except Exception:
        logger.warning("Name suggestion failed, using fallback options", exc_info=True)
    return ["검색 에이전트", "도우미 봇", "어시스턴트"]


def _format_intent_summary(intent: dict[str, Any]) -> str:
    name = intent.get("agent_name_ko") or intent.get("agent_name", "Agent")
    desc = intent.get("agent_description", "")
    return f"- 에이전트 이름: {name}\n- 설명: {desc}"


# ---------------------------------------------------------------------------
# Node: phase2_analyze_intent
# ---------------------------------------------------------------------------


async def phase2_analyze_intent(state: BuilderState) -> dict:
    """LLM 분석 + ask_user pending 카드 emit. intent_confirmed=True면 완료 메시지만.

    실제 다음 노드 라우팅은 graph.py의 ``conditional_edges`` 가 결정한다.
    """
    if not (state.get("user_request") or get_last_user_text(state)):
        return {
            "current_phase": 2,
            "error_message": "사용자 요청이 비어 있습니다.",
        }

    # 사용자가 이미 이름을 확인한 경우 → 완료 메시지만 emit
    # 단, last_revision_message가 set이면 (router로 phase 8에서 재진입한 경우)
    # 다시 LLM 분석 + ask_user 흐름으로 (intent_confirmed=False로 reset)
    if (
        state.get("intent_confirmed")
        and state.get("intent")
        and not state.get("last_revision_message")
    ):
        intent_dict = state["intent"]  # type: ignore[index]
        complete_msgs = build_phase_complete(
            2,
            ensure_todos(state),
            f"좋습니다! 의도 분석이 완료되었습니다.\n\n[Phase 2 완료] 의도 수집\n\n"
            f"{_format_intent_summary(intent_dict)}\n\n"
            f"이제 Phase 3: 도구 추천을 시작하겠습니다.",
        )
        return {
            "messages": complete_msgs,
            "current_phase": 3,
        }

    combined = _build_combined_request(state)

    # LLM 2개 병렬 — 각 ~5-10초 호출 → 절반으로 단축
    try:
        intent_obj, name_options = await asyncio.gather(
            analyze_intent(combined),
            _suggest_name_options(combined),
        )
    except Exception:  # pragma: no cover
        logger.exception("Intent analysis failed")
        return {
            "current_phase": 2,
            "error_message": "의도 분석 중 오류가 발생했습니다.",
        }

    suggested = (intent_obj.agent_name_ko or "").strip()
    if suggested and suggested not in _FALLBACK_NAMES and suggested not in name_options:
        name_options = [suggested] + name_options[:2]
    name_options = name_options[:3]

    # ask_user pending 카드 emit (ToolMessage 없이 AIMessage tool_call만)
    # → frontend UserInputUI가 result undefined로 인식하여 입력 폼 표시
    # NOTE: "직접 입력" 옵션은 의도적으로 제외 — UserInputUI가 single_select 1개
    # 질문에서 옵션 클릭 즉시 라벨 그대로 자동 제출하여 무한 루프가 발생하기 때문.
    # 사용자가 다른 이름을 원하면 Phase 8 router에서 phase 2로 점프 가능.
    msgs, tool_call_id = make_pending_tool_card(
        "ask_user",
        {
            "question": _ASK_QUESTION,
            "options": name_options,
        },
        intro_text="이제 사용자 의도를 분석하겠습니다.",
    )

    return {
        "messages": msgs,
        # intent는 임시 저장 (intent_confirmed=False 상태)
        "intent": intent_obj.model_dump(mode="json"),
        "pending_tool_call_id": tool_call_id,
    }


# ---------------------------------------------------------------------------
# Node: phase2_intent_wait
# ---------------------------------------------------------------------------


async def phase2_intent_wait(state: BuilderState) -> dict:
    """interrupt 호출 → 응답 처리. dict만 반환, 라우팅은 graph.py 가 결정."""
    answer = interrupt(
        {
            "type": "ask_user",
            "question": _ASK_QUESTION,
        }
    )

    answer_text = str(answer or "").strip()
    pending_tc_id = state.get("pending_tool_call_id")

    # 빈 응답 → intent_confirmed=False로 둠 (analyze 재진입)
    if not answer_text:
        close_msgs = close_pending_tool_card(pending_tc_id, "ask_user", "(응답 없음)")
        return {
            "messages": close_msgs,
            "last_revision_message": None,
            "pending_tool_call_id": None,
        }

    intent_dict: dict[str, Any] = dict(state.get("intent") or {})
    intent_dict["agent_name_ko"] = answer_text
    if not intent_dict.get("agent_name") or intent_dict["agent_name"] in _FALLBACK_NAMES:
        intent_dict["agent_name"] = answer_text

    close_msgs = close_pending_tool_card(pending_tc_id, "ask_user", answer_text)
    return {
        "intent": intent_dict,
        "intent_confirmed": True,
        "messages": [*close_msgs, HumanMessage(content=answer_text)],
        "last_revision_message": None,  # router로 들어왔던 revision 소비 완료
        "pending_tool_call_id": None,
    }
