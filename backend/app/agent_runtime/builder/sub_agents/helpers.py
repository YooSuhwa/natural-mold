"""서브에이전트 공통 헬퍼 — LLM JSON retry + fence strip.

4개 서브에이전트(intent_analyzer, tool_recommender, middleware_recommender,
prompt_generator)가 공유하는 패턴을 추출한다.

API 에러(429, 529 등) 발생 시 최대 2회 재시도 후 폴백 모델로 전환한다.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.agent_runtime.model_factory import PROVIDER_API_KEY_MAP, create_chat_model
from app.config import settings

logger = logging.getLogger(__name__)

# API 에러 중 재시도 가능한 상태 코드
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}
_API_MAX_RETRIES = 2
_API_RETRY_DELAY = 2.0  # seconds


def strip_code_fences(text: str) -> str:
    """마크다운 코드 펜스(```…```)를 제거한다."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


@functools.cache
def _get_builder_model() -> BaseChatModel:
    """Builder 기본 모델을 생성한다 (캐시됨)."""
    return create_chat_model(
        settings.builder_model_provider,
        settings.builder_model_name,
        api_key=PROVIDER_API_KEY_MAP.get(settings.builder_model_provider),
    )


@functools.cache
def _get_fallback_model() -> BaseChatModel | None:
    """Builder 폴백 모델을 생성한다 (캐시됨). 기본 모델과 같으면 None."""
    if (
        settings.builder_fallback_provider == settings.builder_model_provider
        and settings.builder_fallback_name == settings.builder_model_name
    ):
        return None
    key = PROVIDER_API_KEY_MAP.get(settings.builder_fallback_provider)
    if not key:
        return None
    return create_chat_model(
        settings.builder_fallback_provider,
        settings.builder_fallback_name,
        api_key=key,
    )


def _is_retryable(exc: Exception) -> bool:
    """재시도 가능한 API 에러인지 판별한다."""
    status = getattr(exc, "status_code", None)
    if status and status in _RETRYABLE_STATUS_CODES:
        return True
    exc_name = type(exc).__name__.lower()
    return any(k in exc_name for k in ("overloaded", "ratelimit", "timeout"))


async def _invoke_with_api_retry(
    model: BaseChatModel,
    messages: list,
    *,
    fallback: BaseChatModel | None = None,
) -> Any:
    """LLM을 호출하되, API 에러 시 재시도 후 폴백 모델로 전환한다."""
    last_exc: Exception | None = None

    for attempt in range(_API_MAX_RETRIES):
        try:
            return await model.ainvoke(messages)
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            logger.warning(
                "API call failed (attempt %d/%d): %s",
                attempt + 1,
                _API_MAX_RETRIES,
                exc,
            )
            if attempt < _API_MAX_RETRIES - 1:
                await asyncio.sleep(_API_RETRY_DELAY * (attempt + 1))

    # 재시도 소진 → 폴백 모델 시도
    if fallback:
        logger.info("Falling back to %s:%s", settings.builder_fallback_provider,
                     settings.builder_fallback_name)
        try:
            return await fallback.ainvoke(messages)
        except Exception as fallback_exc:
            logger.error("Fallback model also failed: %s", fallback_exc)
            raise fallback_exc from last_exc

    raise last_exc  # type: ignore[misc]


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

    API 에러(429, 529 등)는 자동 재시도 후 폴백 모델로 전환.
    JSON 파싱 실패는 프롬프트 수정 후 재시도.

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
    model = _get_builder_model()
    fallback = _get_fallback_model()
    description = task_description

    for attempt in range(max_retries):
        response = await _invoke_with_api_retry(
            model,
            [
                {"role": "system", "content": system_prompt},
                HumanMessage(content=description),
            ],
            fallback=fallback,
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

    API 에러(429, 529 등)는 자동 재시도 후 폴백 모델로 전환.

    Args:
        system_prompt: 시스템 프롬프트
        task_description: 사용자 메시지
        min_length: 최소 응답 길이 (이하이면 재시도)
        retry_suffix_template: 재시도 시 추가할 문자열 ({char_count} 치환)
        max_retries: 최대 시도 횟수

    Returns:
        텍스트 응답. max_retries 초과 시 None.
    """
    model = _get_builder_model()
    fallback = _get_fallback_model()
    description = task_description

    for attempt in range(max_retries):
        response = await _invoke_with_api_retry(
            model,
            [
                {"role": "system", "content": system_prompt},
                HumanMessage(content=description),
            ],
            fallback=fallback,
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
