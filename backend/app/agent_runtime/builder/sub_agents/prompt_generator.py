"""Phase 5 — 프롬프트 생성 서브에이전트.

모든 정보를 종합하여 에이전트의 시스템 프롬프트(마크다운)를 작성한다.
공식 템플릿 구조를 준수한다 (기획서 Section 5.5).
"""

from __future__ import annotations

import logging

from app.agent_runtime.builder.sub_agents.helpers import invoke_for_text, load_prompt
from app.schemas.builder import (
    AgentCreationIntent,
    MiddlewareRecommendation,
    ToolRecommendation,
)

logger = logging.getLogger(__name__)

_FALLBACK_PROMPT = (
    "모든 정보를 종합하여 에이전트의 시스템 프롬프트를 마크다운으로 작성한다. "
    "2000~5000자, 마크다운 형식만, 프롬프트 본문만 반환."
)

SYSTEM_PROMPT = (
    load_prompt("prompt_generator.md") or _FALLBACK_PROMPT
)


def _format_tools(tools: list[ToolRecommendation]) -> str:
    if not tools:
        return "(추천된 도구 없음)"
    lines: list[str] = []
    for i, t in enumerate(tools, 1):
        lines.append(
            f"{i}. {t.tool_name} - {t.description}"
            f" (선택 이유: {t.reason})"
        )
    return "\n".join(lines)


def _format_middlewares(middlewares: list[MiddlewareRecommendation]) -> str:
    if not middlewares:
        return "(추천된 미들웨어 없음)"
    lines: list[str] = []
    for i, m in enumerate(middlewares, 1):
        lines.append(
            f"{i}. {m.middleware_name} - {m.description}"
            f" (선택 이유: {m.reason})"
        )
    return "\n".join(lines)


def _build_task_description(
    intent: AgentCreationIntent,
    tools: list[ToolRecommendation],
    middlewares: list[MiddlewareRecommendation],
) -> str:
    return (
        "다음 모든 정보를 종합하여 고품질의 시스템 프롬프트(마크다운 형식)를\n"
        "생성해주세요. 2000~5000자 범위로 작성하고, 에이전트가 실제로 사용할\n"
        "지침서로서 역할할 수 있어야 합니다.\n\n"
        f"=== AgentCreationIntent ===\n{intent.model_dump_json(indent=2)}\n\n"
        f"=== 추천된 도구 ===\n{_format_tools(tools)}\n\n"
        f"=== 추천된 미들웨어 ===\n{_format_middlewares(middlewares)}\n\n"
        "=== 요구사항 ===\n"
        "- 마크다운 형식\n"
        "- 8개 필수 섹션 + 미들웨어 섹션(해당 시) 순서 준수:\n"
        "  # {Agent Name}\n"
        "  ## Role → ## Language Rule → ## Responsibilities\n"
        "  → ## Tool Guidelines → ## Workflow → ## Error Handling\n"
        "  → ## Constraints → ## Out of Scope\n"
        "  → ## (Middleware-Specific Sections, 해당 시에만)\n"
        "- 각 도구별 Purpose / When(조건 2~4개) / Caution(주의 2~4개)\n"
        "- 도구가 2개 이상이면 도구 간 호출 순서·관계 명시\n"
        "- Workflow: 이해→실행→검증 3단계 루프, 분기를 if/else로 명시\n"
        "- Error Handling: 도구 실패·빈 결과·타임아웃 대응 절차\n"
        "- Out of Scope: 범위 밖 요청 거절 + 안내 패턴\n"
        "- 금지 표현: '적절히 대응', '필요 시', '상황에 따라 판단' 사용 금지\n"
        "- 응답 스타일과 톤 가이드 포함\n"
        "- 복잡한 도구는 호출 예시 필수 포함\n"
        "- TodoListMiddleware 포함 시 write_todos 사용 지침 섹션 필수 추가\n"
        "- 2000~5000자 범위"
    )


# 프롬프트 검증용 핵심 헤딩 (builder/prompts/prompt_generator.md의 필수 섹션과 동기화)
_REQUIRED_HEADINGS = ("## Role", "## Tool Guidelines", "## Workflow", "## Constraints")


def _has_required_sections(text: str) -> bool:
    """생성된 프롬프트에 핵심 헤딩이 포함되어 있는지 확인한다."""
    return all(heading in text for heading in _REQUIRED_HEADINGS)


async def generate_system_prompt(
    intent: AgentCreationIntent,
    tools: list[ToolRecommendation],
    middlewares: list[MiddlewareRecommendation],
) -> str:
    """시스템 프롬프트를 생성한다. 실패 시 1회 재시도 후 기본 프롬프트를 반환한다."""
    description = _build_task_description(intent, tools, middlewares)

    result = await invoke_for_text(SYSTEM_PROMPT, description, min_length=1500)
    if result is not None and _has_required_sections(result):
        return result

    # fallback — 새 8+1 섹션 구조에 맞춘 기본 프롬프트
    tool_guidelines = ""
    for t in tools:
        tool_guidelines += (
            f"### {t.tool_name}\n"
            f"- Purpose: {t.description}\n"
            f"- When: 사용자가 관련 작업을 요청할 때\n"
            f"- Caution: 결과가 없으면 사용자에게 안내\n\n"
        )
    if not tool_guidelines:
        tool_guidelines = "사용 가능한 도구가 없습니다.\n\n"

    mw_section = ""
    if middlewares:
        mw_names = [m.middleware_name for m in middlewares]
        # 레지스트리 키(todo_list) + PascalCase(TodoListMiddleware) 모두 매칭
        # "todo_list" → "todolist", "TodoListMiddleware" → "todolistmiddleware"
        normalized = [n.lower().replace("_", "") for n in mw_names]
        has_todo = any("todolist" in n for n in normalized)
        has_summarization = any("summarization" in n for n in normalized)

        mw_section = f"\n## Middleware\n사용 중인 미들웨어: {', '.join(mw_names)}\n"

        if has_todo:
            mw_section += (
                "\n## 작업 계획 및 실행 (Todo List)\n"
                "복잡한 작업을 수행할 때는 `write_todos` 도구로 "
                "작업 계획을 먼저 수립하세요.\n"
                "1. 사용자 요청을 분석하여 필요한 단계를 파악\n"
                "2. `write_todos`로 작업 계획을 작성\n"
                "3. 계획에 따라 각 단계를 순차적으로 실행\n"
                "4. 각 단계 완료 시 진행 상황을 업데이트\n"
            )
        if has_summarization:
            mw_section += (
                "\n## 대화 요약\n"
                "대화가 길어지면 시스템이 자동으로 이전 내용을 "
                "요약합니다. 직접 제어할 필요는 없습니다.\n"
            )

    return (
        f"# {intent.agent_name}\n\n"
        f"## Role\n{intent.agent_description}\n\n"
        "## Language Rule\n"
        "사용자의 질문 언어와 동일한 언어로 응답한다.\n\n"
        "## Responsibilities\n"
        f"1. {intent.primary_task_type}\n\n"
        f"## Tool Guidelines\n{tool_guidelines}"
        "## Workflow\n"
        "1. 사용자 요청을 분석하여 의도를 파악합니다.\n"
        "2. 요청에 맞는 도구를 호출하여 작업을 수행합니다.\n"
        "3. 결과를 확인하고, 부족하면 재시도합니다.\n"
        "4. 결과를 정리하여 사용자에게 응답합니다.\n\n"
        "## Error Handling\n"
        "- 도구 호출 실패 시 1회 재시도 후 오류를 안내합니다.\n"
        "- 검색 결과가 없으면 키워드를 변경하여 재검색합니다.\n\n"
        "## Constraints\n"
        f"- ALWAYS: {intent.response_tone}으로 응답\n"
        "- NEVER: 확인되지 않은 정보를 사실처럼 전달하지 않는다\n\n"
        "## Out of Scope\n"
        "- 역할 범위를 벗어나는 요청은 정중히 거절하고, "
        "가능한 작업을 안내합니다.\n"
        f"{mw_section}"
    )
