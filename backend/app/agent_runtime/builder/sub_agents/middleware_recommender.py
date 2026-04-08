"""Phase 4 — 미들웨어 추천 서브에이전트.

AgentCreationIntent + 도구 목록을 분석하여 미들웨어를 추천한다.
사용 가능한 미들웨어 카탈로그를 동적으로 주입한다 (AD-7).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agent_runtime.builder.sub_agents.helpers import invoke_with_json_retry
from app.schemas.builder import (
    AgentCreationIntent,
    MiddlewareRecommendation,
    ToolRecommendation,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
# 미들웨어 추천 에이전트 — 시스템 프롬프트

## 역할
AgentCreationIntent와 도구 목록을 분석하여 적합한 미들웨어를 추천한다.

## 선택 기준
1. 외부 API 도구 존재 → ToolRetryMiddleware 거의 필수
2. 긴 대화 예상 → SummarizationMiddleware 추천
3. 복잡한 다단계 작업 → TodoListMiddleware 추천
4. 빈번한 API 호출 → RateLimiter 또는 Cache 추천
5. 민감 데이터 처리 → InputSanitizer + OutputFilter 추천
6. 최소 1개, 최대 5개 범위

## 특별 규칙
- TodoListMiddleware 추천 시: 반드시 reason에 \
"시스템 프롬프트에 write_todos 사용 지침 추가 필요"라고 명시할 것

## 출력 형식
JSON 배열만 반환:
[
  {
    "middleware_name": "미들웨어 레지스트리 키 (카탈로그의 type 값 정확히 사용)",
    "description": "한 줄 설명",
    "reason": "선택 이유"
  }
]

## 주의사항
- 카탈로그에 없는 미들웨어를 추천하지 않는다.
- provider_specific 미들웨어는 해당 provider를 사용할 때만 추천한다.
- JSON 외 다른 텍스트를 포함하지 않는다.\
"""


def _format_catalog(middlewares_catalog: list[dict[str, Any]]) -> str:
    if not middlewares_catalog:
        return "(사용 가능한 미들웨어가 없습니다)"
    lines: list[str] = []
    for m in middlewares_catalog:
        mtype = m.get("type", "")
        name = m.get("name", "")
        desc = m.get("description", "")
        category = m.get("category", "")
        provider = m.get("provider_specific")
        suffix = f" [provider: {provider}]" if provider else ""
        lines.append(f"- {mtype} ({name}, {category}): {desc}{suffix}")
    return "\n".join(lines)


def _build_task_description(
    intent: AgentCreationIntent,
    tools: list[ToolRecommendation],
    middlewares_catalog: list[dict[str, Any]],
) -> str:
    tool_names = [t.tool_name for t in tools]
    catalog_text = _format_catalog(middlewares_catalog)
    return (
        "다음 정보를 분석하여 필요한 미들웨어들을 추천해주세요:\n\n"
        f"AgentCreationIntent:\n{intent.model_dump_json(indent=2)}\n\n"
        f"추천된 도구들: {json.dumps(tool_names, ensure_ascii=False)}\n\n"
        f"## 사용 가능한 미들웨어 카탈로그\n{catalog_text}\n\n"
        "위 카탈로그에서 에이전트의 성능, 보안, 안정성을 고려한 "
        "미들웨어들을 추천해주세요.\n"
        "각 미들웨어에 대해 middleware_name, description, reason을 "
        "포함한 JSON 배열로 반환해주세요."
    )


async def recommend_middlewares(
    intent: AgentCreationIntent,
    tools: list[ToolRecommendation],
    middlewares_catalog: list[dict[str, Any]],
) -> list[MiddlewareRecommendation]:
    """Intent + 도구 기반으로 미들웨어를 추천한다."""
    description = _build_task_description(intent, tools, middlewares_catalog)

    valid_types = {m.get("type", "").lower() for m in middlewares_catalog}

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

        recommendations: list[MiddlewareRecommendation] = []
        for item in raw_list:
            name = item.get("middleware_name", "")
            if name.lower() in valid_types:
                recommendations.append(MiddlewareRecommendation(**item))
            else:
                logger.warning("Filtered out non-existent middleware: %s", name)
        return recommendations
    except (ValueError, TypeError) as exc:
        logger.error("Middleware recommendation failed after retries: %s", exc)
        return []
