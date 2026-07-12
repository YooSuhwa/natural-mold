"""빈 응답 재시도 미들웨어 + 기본 신뢰성 미들웨어 조립 (BE-S10 분리)."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from app.agent_runtime.runtime.models import _is_retryable_model_error


def _has_visible_ai_content(response: ModelResponse[Any] | AIMessage) -> bool:
    messages = [response] if isinstance(response, AIMessage) else list(response.result)
    for message in messages:
        if getattr(message, "type", None) != "ai":
            continue
        if getattr(message, "tool_calls", None):
            return True
        content = getattr(message, "content", None)
        if isinstance(content, str):
            if content.strip():
                return True
            continue
        if isinstance(content, list):
            for block in content:
                if isinstance(block, str) and block.strip():
                    return True
                if isinstance(block, dict) and str(block.get("text") or "").strip():
                    return True
    return False


class EmptyContentRetryMiddleware(AgentMiddleware):
    """Retry model calls that return an empty assistant message without tool calls."""

    def __init__(self, *, max_retries: int = 1) -> None:
        super().__init__()
        self.max_retries = max(0, max_retries)
        self.tools = []

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        response = None
        for attempt in range(self.max_retries + 1):
            response = handler(request)
            if _has_visible_ai_content(response) or attempt >= self.max_retries:
                return response
        return response

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        response = None
        for attempt in range(self.max_retries + 1):
            response = await handler(request)
            if _has_visible_ai_content(response) or attempt >= self.max_retries:
                return response
        return response


def _build_default_reliability_middleware(
    model_candidates: list[BaseChatModel],
    *,
    configured_types: set[str],
) -> list[Any]:
    from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware

    middleware: list[Any] = []
    if len(model_candidates) > 1:
        middleware.append(ModelFallbackMiddleware(*model_candidates[1:]))
    if "model_retry" not in configured_types:
        middleware.append(
            ModelRetryMiddleware(
                max_retries=2,
                retry_on=_is_retryable_model_error,
                on_failure="error",
                initial_delay=1.0,
                backoff_factor=2.0,
                max_delay=60.0,
                jitter=True,
            )
        )
    middleware.append(EmptyContentRetryMiddleware(max_retries=1))
    return middleware
