"""Router 노드 — Phase 8 수정요청 시 어느 phase로 점프할지 분류.

Pydantic enum + 구조화 출력으로 LLM이 잘못된 분기를 하지 못하도록 강제.
모호하면 ask_user fallback.
"""

from __future__ import annotations

import logging

from langgraph.types import Command, interrupt

from app.agent_runtime.builder.sub_agents.helpers import invoke_with_json_retry
from app.agent_runtime.builder_v3.state import BuilderState

logger = logging.getLogger(__name__)


_VALID_TARGETS = {
    "phase2_analyze_intent",
    "phase3_recommend_tools",
    "phase4_recommend_middlewares",
    "phase5_generate_prompt",
    "phase6_choice_propose",
}

_LABEL_TO_NODE = {
    "intent": "phase2_analyze_intent",
    "tools": "phase3_recommend_tools",
    "middlewares": "phase4_recommend_middlewares",
    "prompt": "phase5_generate_prompt",
    "image": "phase6_choice_propose",
}

_SYSTEM_PROMPT = (
    "당신은 에이전트 빌더의 라우팅 분류기입니다. "
    "사용자의 수정 요청 메시지를 보고, 어느 단계를 다시 실행할지 결정합니다. "
    "응답은 반드시 다음 5개 중 하나의 라벨만을 포함한 JSON 객체로만 합니다:\n"
    " - intent: 에이전트 이름/설명/역할 변경\n"
    " - tools: 도구 추천 변경\n"
    " - middlewares: 미들웨어 추천 변경\n"
    " - prompt: 시스템 프롬프트 수정\n"
    " - image: 에이전트 이미지 변경\n\n"
    "응답 형식: {\"target\": \"<label>\", \"reason\": \"<짧은 설명>\"}"
)


async def _classify_target(message: str) -> str | None:
    task = (
        f"사용자 요청: '{message}'\n\n"
        "이 요청은 빌더의 어느 단계를 다시 실행해야 하나요? "
        "intent / tools / middlewares / prompt / image 중 하나의 라벨로 답하세요."
    )
    try:
        result = await invoke_with_json_retry(_SYSTEM_PROMPT, task, max_retries=1)
        if isinstance(result, dict):
            label = str(result.get("target") or "").strip().lower()
            return _LABEL_TO_NODE.get(label)
    except Exception:
        logger.warning("Router classification failed", exc_info=True)
    return None


async def router(state: BuilderState) -> Command:
    message = state.get("last_revision_message") or ""
    target = await _classify_target(message) if message else None

    if target and target in _VALID_TARGETS:
        return Command(
            goto=target,
            update={
                "last_router_decision": target,
                "last_revision_message": message,
            },
        )

    # 모호 → ask_user fallback
    answer = interrupt(
        {
            "type": "ask_user",
            "question": "어느 단계를 수정하시겠어요?",
            "options": [
                "에이전트 이름/설명",
                "도구 추천",
                "미들웨어 추천",
                "시스템 프롬프트",
                "에이전트 이미지",
            ],
        }
    )

    text = str(answer or "").lower()
    fallback_target = "phase3_recommend_tools"  # 가장 흔한 케이스
    if "이름" in text or "설명" in text or "intent" in text:
        fallback_target = "phase2_analyze_intent"
    elif "도구" in text or "tool" in text:
        fallback_target = "phase3_recommend_tools"
    elif "미들웨어" in text or "middleware" in text:
        fallback_target = "phase4_recommend_middlewares"
    elif "프롬프트" in text or "prompt" in text:
        fallback_target = "phase5_generate_prompt"
    elif "이미지" in text or "image" in text:
        fallback_target = "phase6_choice_propose"

    return Command(
        goto=fallback_target,
        update={
            "last_router_decision": fallback_target,
        },
    )
