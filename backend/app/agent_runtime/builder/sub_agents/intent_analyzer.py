"""Phase 2 — 의도 분석 서브에이전트.

사용자의 자연어 요청을 AgentCreationIntent JSON으로 변환한다.
"""

from __future__ import annotations

import logging

from app.agent_runtime.builder.sub_agents.helpers import invoke_with_json_retry
from app.schemas.builder import AgentCreationIntent

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
# 의도 분석 에이전트 — 시스템 프롬프트

## 역할
당신은 AI 에이전트 생성을 위한 의도 분석 전문가입니다.
사용자의 자연어 요청을 받아 에이전트를 만들기 위해 필요한 모든 정보를
체계적으로 분석하고 구조화합니다.

## 출력 형식 (AgentCreationIntent)
반드시 아래 JSON 형식으로만 응답한다:

{
  "agent_name": "영문 에이전트 이름",
  "agent_name_ko": "한글 에이전트 이름",
  "agent_description": "에이전트의 역할과 기능에 대한 상세 설명 (3~5문장)",
  "primary_task_type": "에이전트의 핵심 작업을 한 문장으로 기술",
  "tool_preferences": "선호하는 도구 유형이나 API 종류",
  "output_style": "결과물의 형태 (요약, 리포트, 목록 등)",
  "response_tone": "응답의 톤과 스타일",
  "use_cases": ["사용 사례 1", "사용 사례 2", "사용 사례 3"],
  "constraints": ["제약 조건"],
  "required_capabilities": ["필수 기능 1", "필수 기능 2"]
}

## 추론 가이드라인
- "검색 에이전트"만 언급 → 일반 웹 검색 + 뉴스 검색을 기본 포함
- "번역 에이전트"만 언급 → 다국어 번역 기본, 한국어↔영어 우선
- "코딩 에이전트"만 언급 → 코드 생성 + 디버깅 + 설명 기본
- 톤 미명시 → "친근하고 캐주얼한 어조" 기본값
- output_style 미명시 → "간단한 요약과 주요 포인트" 기본값

## 주의사항
- 사용자에게 추가 질문하지 않는다. 주어진 정보만으로 최선의 분석 수행.
- 모호한 요청도 합리적 기본값을 채워 완전한 Intent를 반환.
- JSON 외 다른 텍스트를 포함하지 않는다.\
"""


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
