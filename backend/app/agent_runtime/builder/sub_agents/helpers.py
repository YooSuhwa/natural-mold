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
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.agent_runtime.model_factory import create_chat_model
from app.database import async_session
from app.services.system_credential_resolver import (
    ResolvedSystemModel,
    SystemModelNotConfiguredError,
    resolve_system_model,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 프롬프트 파일 로더
# ---------------------------------------------------------------------------

# __file__ = backend/app/agent_runtime/builder/sub_agents/helpers.py
# .parent = sub_agents/, .parent.parent = builder/ (prompts/ 디렉토리가 여기에 위치)
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@functools.cache
def load_prompt(filename: str) -> str | None:
    """builder/prompts/ 디렉토리에서 프롬프트 파일을 로드한다 (캐시됨).

    파일이 없으면 None을 반환하여 호출자가 fallback을 사용하게 한다.
    """
    path = _PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s, using fallback", path)
        return None


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


# ADR-019: builder text models come from the operator-selected system LLM
# settings (``text_primary`` / ``text_fallback``), not ``.env``. The old
# ``@functools.cache`` singletons hid runtime setting changes, so we cache by
# the *resolved selection* instead: when the operator changes the credential or
# model, the ``ResolvedSystemModel`` value differs and the chat model is rebuilt.
# This keeps the ~5-10ms ``create_chat_model`` (httpx + SSL) cost off the hot
# path without pinning a stale model across setting changes.
_MODEL_CACHE: dict[str, tuple[ResolvedSystemModel, BaseChatModel]] = {}


async def _resolve_cached_model(role: str) -> BaseChatModel:
    """Resolve + build (or reuse) the chat model for a system ``role``.

    Raises ``SystemModelNotConfiguredError`` when the role is unconfigured.
    """
    async with async_session() as db:
        resolved = await resolve_system_model(db, role)
    cached = _MODEL_CACHE.get(role)
    if cached is not None and cached[0] == resolved:
        return cached[1]
    model = create_chat_model(
        resolved.provider,
        resolved.model_name,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
    )
    _MODEL_CACHE[role] = (resolved, model)
    return model


async def _get_builder_model() -> BaseChatModel:
    """Builder 기본 모델 (system role ``text_primary``)."""
    return await _resolve_cached_model("text_primary")


async def _get_fallback_model() -> BaseChatModel | None:
    """Builder 폴백 모델 (system role ``text_fallback``). 미설정 시 None."""
    try:
        return await _resolve_cached_model("text_fallback")
    except SystemModelNotConfiguredError:
        return None


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
    """LLM을 호출하되, API 에러 시 재시도 후 폴백 모델로 전환한다.

    `builder:internal` tag를 부여하여, 상위 streaming.py가 sub-LLM 응답
    chunk를 사용자 화면 stream에서 제외할 수 있도록 한다.
    """
    last_exc: Exception | None = None
    invoke_config: RunnableConfig = {"tags": ["builder:internal"]}

    for attempt in range(_API_MAX_RETRIES):
        try:
            return await model.ainvoke(messages, config=invoke_config)
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
        logger.info("Falling back to system text_fallback model")
        try:
            return await fallback.ainvoke(messages, config=invoke_config)
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
    model = await _get_builder_model()
    fallback = await _get_fallback_model()
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
            text = strip_code_fences(content).lstrip()
            # 일부 모델(LiteLLM gateway 등)은 JSON 객체 뒤에 설명 텍스트를
            # 덧붙인다. raw_decode로 첫 JSON 값만 파싱하고 뒤따르는 extra data는
            # 무시해 "Extra data: line N" 파싱 실패를 방지한다.
            obj, _ = json.JSONDecoder().raw_decode(text)
            return obj
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
    model = await _get_builder_model()
    fallback = await _get_fallback_model()
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
