"""Phase 5 — 프롬프트 생성 서브에이전트.

모든 정보를 종합하여 에이전트의 시스템 프롬프트(마크다운)를 작성한다.
공식 템플릿 구조를 준수한다 (기획서 Section 5.5).
"""

from __future__ import annotations

import logging

from app.agent_runtime.builder.sub_agents.helpers import invoke_for_text
from app.schemas.builder import (
    AgentCreationIntent,
    MiddlewareRecommendation,
    ToolRecommendation,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
# 프롬프트 생성 에이전트 — 시스템 프롬프트

## 역할
모든 정보를 종합하여 에이전트가 즉시 사용할 수 있는
고품질 시스템 프롬프트를 마크다운으로 작성한다.

## 필수 포함 섹션 (공식 템플릿 준수)

### 1. Role (역할)
- 에이전트 이름과 핵심 역할을 1~2문장으로 정의

### 2. Responsibilities (핵심 책임)
- 번호 목록으로 주요 작업 3~5가지 기술

### 3. Tool Guidelines (도구 가이드)
- 각 도구별로:
  - `{tool_name}`: Purpose, When (사용 조건), Caution (주의사항)
  - 호출 예시 포함 권장

### 4. Workflow (작업 흐름)
- 사용자 요청 수신 시 따라야 할 단계별 절차
- 의사결정 로직 포함 (어떤 도구를 언제 선택할지)

### 5. Constraints (제약 조건)
- ALWAYS: 필수 행동 목록
- NEVER: 금지 행동 목록

### 6. (미들웨어 특수 섹션)
- TodoListMiddleware 포함 시: "작업 계획 및 실행" 섹션 필수 추가
- SummarizationMiddleware 포함 시: 에이전트가 이를 인지하되 직접 제어하지 않음 명시

## 프롬프트 품질 기준
1. 명확성: 모호한 표현 대신 구체적 행동 지침
2. 구체성: "적절히 대응" 대신 정확한 절차 기술
3. 완전성: 도구 사용법, 오류 처리, 응답 스타일 모두 포함
4. 실용성: 실제 사용 시나리오 예시 포함

## 제약
- 분량: 2000~5000자
- 언어: 에이전트 설명 언어와 동일 (한글 설명이면 한글로)
- 마크다운 형식만. JSON/YAML 포함 금지.
- 프롬프트만 반환. 부가 설명 금지.\
"""


def _format_tools(tools: list[ToolRecommendation]) -> str:
    if not tools:
        return "(추천된 도구 없음)"
    lines: list[str] = []
    for i, t in enumerate(tools, 1):
        lines.append(f"{i}. {t.tool_name} - {t.description}")
    return "\n".join(lines)


def _format_middlewares(middlewares: list[MiddlewareRecommendation]) -> str:
    if not middlewares:
        return "(추천된 미들웨어 없음)"
    lines: list[str] = []
    for i, m in enumerate(middlewares, 1):
        lines.append(f"{i}. {m.middleware_name} - {m.description}")
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
        "- 공식 템플릿 구조 준수:\n"
        "  # {Agent Name}\n"
        "  ## Role -> ## Responsibilities -> ## Tool Guidelines -> "
        "## Workflow -> ## Constraints\n"
        "- 각 도구별 Purpose / When / Caution 포함\n"
        "- 응답 스타일과 톤 가이드 포함\n"
        "- 실제 동작 가능한 구체적 지침 포함\n"
        "- TodoListMiddleware 포함 시 write_todos 사용 지침 섹션 필수 추가\n"
        "- 2000~5000자 범위"
    )


async def generate_system_prompt(
    intent: AgentCreationIntent,
    tools: list[ToolRecommendation],
    middlewares: list[MiddlewareRecommendation],
) -> str:
    """시스템 프롬프트를 생성한다. 실패 시 1회 재시도 후 기본 프롬프트를 반환한다."""
    description = _build_task_description(intent, tools, middlewares)

    result = await invoke_for_text(SYSTEM_PROMPT, description)
    if result is not None:
        return result

    # fallback
    tool_names = ", ".join(t.tool_name for t in tools) or "없음"
    return (
        f"# {intent.agent_name}\n\n"
        f"## Role\n{intent.agent_description}\n\n"
        "## Responsibilities\n"
        f"1. {intent.primary_task_type}\n\n"
        f"## Tool Guidelines\n사용 가능한 도구: {tool_names}\n\n"
        "## Workflow\n1. 사용자 요청을 분석합니다.\n"
        "2. 적절한 도구를 선택하여 작업을 수행합니다.\n"
        "3. 결과를 정리하여 응답합니다.\n\n"
        "## Constraints\n"
        f"- ALWAYS: {intent.response_tone}으로 응답\n"
        "- NEVER: 확인되지 않은 정보를 사실처럼 전달\n"
    )
