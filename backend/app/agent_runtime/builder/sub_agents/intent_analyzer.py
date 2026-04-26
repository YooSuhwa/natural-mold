"""Phase 2 — 의도 분석 서브에이전트.

사용자의 자연어 요청을 AgentCreationIntent JSON으로 변환한다.
"""

from __future__ import annotations

import logging

from app.agent_runtime.builder.sub_agents.helpers import invoke_with_json_retry, load_prompt
from app.schemas.builder import AgentCreationIntent

logger = logging.getLogger(__name__)

_FALLBACK_PROMPT = (
    "당신은 AI 에이전트 생성을 위한 의도 분석 전문가입니다. "
    "사용자의 자연어 요청을 AgentCreationIntent JSON으로 변환합니다. "
    "JSON 외 다른 텍스트를 포함하지 않습니다."
)

SYSTEM_PROMPT = load_prompt("intent_analyzer.md") or _FALLBACK_PROMPT


def _build_task_description(user_request: str) -> str:
    return (
        f'사용자가 "{user_request}"라고 요청했습니다.\n'
        "다음 정보를 수집해주세요:\n\n"
        "1. 에이전트 이름 (영문과 한글)\n"
        "2. 에이전트 설명 (상세한 기능 설명)\n"
        "3. 주요 작업 유형 (primary_task_type)\n"
        "4. 에이전트의 주요 기능들\n"
        "5. 사용자가 원하는 기능의 특징\n\n"
        "사용자의 요청을 정리하고 AgentCreationIntent 형식으로 반환해주세요."
    )


async def analyze_intent(user_request: str) -> AgentCreationIntent:
    """사용자 요청을 분석하여 AgentCreationIntent를 반환한다.

    파싱 실패 시 1회 재시도 후 기본 Intent를 반환한다.
    """
    description = _build_task_description(user_request)

    try:
        data = await invoke_with_json_retry(SYSTEM_PROMPT, description)
        return AgentCreationIntent(**data)
    except (ValueError, TypeError) as exc:
        logger.error("Intent parsing failed after retries: %s, using fallback", exc)

    # fallback
    return AgentCreationIntent(
        agent_name="Custom Agent",
        agent_name_ko="맞춤 에이전트",
        agent_description=f"사용자 요청에 따라 생성된 에이전트: {user_request}",
        primary_task_type="일반 작업 수행",
        use_cases=["사용자 요청 처리"],
        required_capabilities=["일반 대화"],
    )
