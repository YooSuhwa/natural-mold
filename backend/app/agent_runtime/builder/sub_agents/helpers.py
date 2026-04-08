"""서브에이전트 공통 헬퍼 — LLM JSON retry + fence strip.

4개 서브에이전트(intent_analyzer, tool_recommender, middleware_recommender,
prompt_generator)가 공유하는 패턴을 추출한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent_runtime.model_factory import create_chat_model
from app.config import settings

logger = logging.getLogger(__name__)


def strip_code_fences(text: str) -> str:
    """마크다운 코드 펜스(```…```)를 제거한다."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


async def invoke_with_json_retry(
    system_prompt: str,
    task_description: str,
    *,
    retry_suffix: str = (
        "\n\n[시스템] 이전 응답이 유효한 JSON이 아니었습니다. 반드시 JSON 형식으로만 응답해주세요."
    ),
    max_retries: int = 2,
) -> Any:
    """LLM을 호출하고 JSON 파싱을 시도한다. 실패 시 재시도한다.

    Args:
        system_prompt: 시스템 프롬프트
        task_description: 사용자 메시지 (재시도 시 suffix 추가)
        retry_suffix: 파싱 실패 시 task_description에 추가할 문자열
        max_retries: 최대 시도 횟수

    Returns:
        파싱된 JSON 객체 (dict 또는 list)

    Raises:
        ValueError: max_retries 횟수만큼 파싱 실패 시
    """
    model = create_chat_model(settings.builder_model_provider, settings.builder_model_name)
    description = task_description

    for attempt in range(max_retries):
        response = await model.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                HumanMessage(content=description),
            ]
        )
        content = response.content
        try:
            text = strip_code_fences(content)
            return json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("JSON parsing failed (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                description += retry_suffix

    raise ValueError(f"JSON parsing failed after {max_retries} attempts")


async def invoke_for_text(
    system_prompt: str,
    task_description: str,
    *,
    min_length: int = 500,
    retry_suffix_template: str = (
        "\n\n[시스템] 이전 응답이 너무 짧았습니다 ({char_count}자). "
        "2000~5000자 범위로 작성해주세요."
    ),
    max_retries: int = 2,
) -> str | None:
    """LLM을 호출하고 텍스트 응답을 반환한다. 짧으면 재시도한다.

    Args:
        system_prompt: 시스템 프롬프트
        task_description: 사용자 메시지
        min_length: 최소 응답 길이 (이하이면 재시도)
        retry_suffix_template: 재시도 시 추가할 문자열 ({char_count} 치환)
        max_retries: 최대 시도 횟수

    Returns:
        텍스트 응답. max_retries 초과 시 None.
    """
    model = create_chat_model(settings.builder_model_provider, settings.builder_model_name)
    description = task_description

    for attempt in range(max_retries):
        response = await model.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                HumanMessage(content=description),
            ]
        )
        prompt_text = response.content.strip()

        char_count = len(prompt_text)
        if char_count < min_length:
            logger.warning(
                "Generated text too short (%d chars, attempt %d)",
                char_count,
                attempt + 1,
            )
            if attempt < max_retries - 1:
                description += retry_suffix_template.format(char_count=char_count)
                continue
        return prompt_text

    return None
