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
    """카탈로그를 텍스트로 포맷한다 — ``Tool`` / ``McpTool`` / ``Skill`` 모두 포함.

    LLM 이 종류를 인지해 적절한 ``kind`` 를 응답에 포함할 수 있도록 ``[kind]``
    prefix 를 붙인다.
    """
    if not tools_catalog:
        return "(사용 가능한 항목이 없습니다)"
    lines: list[str] = []
    for t in tools_catalog:
        name = t.get("name", "")
        desc = t.get("description", "")
        kind = t.get("kind", "tool")
        lines.append(f"- [{kind}] {name}: {desc}")
    return "\n".join(lines)


def _build_task_description(
    intent: AgentCreationIntent,
    tools_catalog: list[dict[str, Any]],
    *,
    previous_recommendations: list[dict[str, Any]] | None = None,
    revision_message: str | None = None,
) -> str:
    catalog_text = _format_catalog(tools_catalog)
    sections = [
        "다음 AgentCreationIntent를 분석하여 필요한 항목들을 추천해주세요:",
        "",
        f"## AgentCreationIntent\n{intent.model_dump_json(indent=2)}",
        f"## 사용 가능한 카탈로그\n{catalog_text}",
    ]
    if previous_recommendations:
        prev_text = "\n".join(
            f"- [{p.get('kind', 'tool')}] {p.get('tool_name', '')}: {p.get('reason', '')}"
            for p in previous_recommendations
        )
        sections.append(f"## 직전 추천 (수정 대상)\n{prev_text}")
    if revision_message:
        # 수정 메시지는 LLM 추론을 override 하는 절대 지시. 원래 intent 보다
        # 우선하며, 수치/한정 표현 (e.g. "이것만", "X 빼고") 은 정확히 반영.
        sections.append(
            "## 사용자 수정 요청 (절대 우선)\n"
            f"{revision_message}\n\n"
            "위 수정 요청은 원래 intent 보다 절대 우선합니다. "
            "사용자가 특정 항목명을 명시하면 그 집합을 정확히 따르고, "
            "보조/지원 도구를 임의 추가하지 않습니다. "
            "사용자가 \"이것만\", \"~만\", \"X 빼고\" 같은 한정 표현을 쓰면 "
            "그 의미를 그대로 반영합니다."
        )
    sections.append(
        "응답: tool_name, kind, description, reason 을 포함한 JSON 배열만."
    )
    return "\n\n".join(sections)


async def recommend_tools(
    intent: AgentCreationIntent,
    tools_catalog: list[dict[str, Any]],
    *,
    previous_recommendations: list[dict[str, Any]] | None = None,
    revision_message: str | None = None,
) -> list[ToolRecommendation]:
    """Intent 기반으로 항목 (Tool / McpTool / Skill) 을 추천한다.

    파싱 실패 시 빈 리스트. 카탈로그에 없는 이름이거나 (이름, kind) 조합이
    카탈로그와 다르면 silent drop — LLM 환각 가드.

    ``previous_recommendations`` + ``revision_message`` 가 함께 주어지면
    수정 컨텍스트를 LLM 에 전달 — 사용자가 "이것만 / X 빼고" 같은 한정
    표현을 쓸 때 정확히 반영하도록 프롬프트에 절대 우선 섹션 삽입.
    """
    description = _build_task_description(
        intent,
        tools_catalog,
        previous_recommendations=previous_recommendations,
        revision_message=revision_message,
    )

    # 카탈로그 (이름 → 정규 kind) 인덱스. LLM 이 kind 를 누락하거나 잘못 답하면
    # 카탈로그 값으로 정정해 confirm 단계가 올바른 테이블을 매칭하도록 한다.
    name_to_kind: dict[str, str] = {
        t.get("name", "").lower(): t.get("kind", "tool") for t in tools_catalog
    }

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

        recommendations: list[ToolRecommendation] = []
        for item in raw_list:
            name = item.get("tool_name", "")
            canonical_kind = name_to_kind.get(name.lower())
            if canonical_kind is None:
                logger.warning("Filtered out non-existent item: %s", name)
                continue
            # LLM 이 답한 kind 보다 카탈로그 정답 우선 — 환각 방지
            item["kind"] = canonical_kind
            recommendations.append(ToolRecommendation(**item))
        return recommendations
    except (ValueError, TypeError) as exc:
        logger.error("Tool recommendation failed after retries: %s", exc)
        return []
