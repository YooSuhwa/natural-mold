"""Phase 3 — 도구 추천 서브에이전트.

AgentCreationIntent를 분석하여 에이전트에 필요한 도구를 추천한다.
사용 가능한 도구 카탈로그를 동적으로 주입한다 (AD-7).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent_runtime.builder.sub_agents.helpers import invoke_with_json_retry
from app.schemas.builder import AgentCreationIntent, ToolRecommendation

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
# 도구 추천 에이전트 — 시스템 프롬프트

## 역할
AgentCreationIntent를 분석하여 에이전트에 가장 적합한 도구를 추천한다.

## 선택 기준
1. **필수성:** primary_task_type 수행에 반드시 필요한가?
2. **적합성:** use_cases와 required_capabilities를 충족하는가?
3. **최소성:** 3~5개 적정. 불필요한 도구 금지.
4. **다양성:** 서로 보완하는 도구 조합 선호.
5. **사용자 선호:** tool_preferences 명시 시 우선 반영.

## 주의: 유사 도구 선택 기준
동일 기능의 도구가 복수 존재할 때:
- 최신 뉴스/일반 정보 → 범용 검색 도구 우선
- 한국 특화 필요시 → 네이버 검색 도구 추가
- 개념/문맥 기반 → 시맨틱 검색 도구

## 출력 형식
JSON 배열만 반환:
[
  {
    "tool_name": "고유 식별자 (카탈로그의 name 값 정확히 사용)",
    "description": "한 줄 설명",
    "reason": "선택 이유"
  }
]

## 주의사항
- 카탈로그에 없는 도구를 추천하지 않는다.
- JSON 외 다른 텍스트를 포함하지 않는다.\
"""


def _format_catalog(tools_catalog: list[dict[str, Any]]) -> str:
    """도구 카탈로그를 텍스트로 포맷한다."""
    if not tools_catalog:
        return "(사용 가능한 도구가 없습니다)"
    lines: list[str] = []
    for t in tools_catalog:
        name = t.get("name", "")
        desc = t.get("description", "")
        tool_type = t.get("type", "")
        lines.append(f"- {name} ({tool_type}): {desc}")
    return "\n".join(lines)


def _build_task_description(
    intent: AgentCreationIntent,
    tools_catalog: list[dict[str, Any]],
) -> str:
    catalog_text = _format_catalog(tools_catalog)
    return (
        "다음 AgentCreationIntent를 분석하여 필요한 도구들을 추천해주세요:\n\n"
        f"AgentCreationIntent:\n{intent.model_dump_json(indent=2)}\n\n"
        f"## 사용 가능한 도구 카탈로그\n{catalog_text}\n\n"
        "위 카탈로그에서 이 에이전트에 필요한 도구들을 추천해주세요.\n"
        "각 도구에 대해 tool_name, description, reason을 포함한 JSON 배열로 반환해주세요."
    )


async def recommend_tools(
    intent: AgentCreationIntent,
    tools_catalog: list[dict[str, Any]],
) -> list[ToolRecommendation]:
    """Intent 기반으로 도구를 추천한다. 파싱 실패 시 빈 리스트를 반환한다."""
    description = _build_task_description(intent, tools_catalog)

    # 카탈로그에 있는 유효한 도구 이름
    valid_names = {t.get("name", "").lower() for t in tools_catalog}

    try:
        raw_list = await invoke_with_json_retry(
            SYSTEM_PROMPT,
            description,
            retry_suffix=(
                "\n\n[시스템] 이전 응답이 유효한 JSON 배열이 아니었습니다. "
                "반드시 JSON 배열 형식으로만 응답해주세요."
            ),
        )
        if not isinstance(raw_list, list):
            raise ValueError("Expected JSON array")

        # 카탈로그에 없는 도구 필터링 (기획서 9.1 대응)
        recommendations: list[ToolRecommendation] = []
        for item in raw_list:
            name = item.get("tool_name", "")
            if name.lower() in valid_names:
                recommendations.append(ToolRecommendation(**item))
            else:
                logger.warning("Filtered out non-existent tool: %s", name)
        return recommendations
    except (ValueError, TypeError) as exc:
        logger.error("Tool recommendation failed after retries: %s", exc)
        return []
