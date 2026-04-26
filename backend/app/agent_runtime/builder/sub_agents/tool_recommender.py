"""Phase 3 — 도구 추천 서브에이전트.

AgentCreationIntent를 분석하여 에이전트에 필요한 도구를 추천한다.
사용 가능한 도구 카탈로그를 동적으로 주입한다 (AD-7).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent_runtime.builder.sub_agents.helpers import invoke_with_json_retry, load_prompt
from app.schemas.builder import AgentCreationIntent, ToolRecommendation

logger = logging.getLogger(__name__)

_FALLBACK_PROMPT = (
    "AgentCreationIntent를 분석하여 에이전트에 적합한 도구를 추천한다. "
    "카탈로그에 있는 도구만 추천하고, JSON 배열로만 응답한다."
)

SYSTEM_PROMPT = load_prompt("tool_recommender.md") or _FALLBACK_PROMPT


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
