"""빈 응답 재시도 미들웨어 + 기본 신뢰성 미들웨어 조립 (BE-S10 분리)."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.language_models import BaseChatModel

from app.agent_runtime.runtime.model_response_content import (
    has_visible_ai_content as _has_visible_ai_content,
)
from app.agent_runtime.runtime.models import _is_retryable_model_error


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
        # An opted-in lease stream owns its entire retry budget. A plain
        # non-streaming empty answer still uses the existing recovery path.
        observe = getattr(request.model, "observe_stream_retry_budget", None)
        scope = cast(AbstractContextManager[Any], observe()) if callable(observe) else nullcontext()
        with scope as observation:
            response = None
            for attempt in range(self.max_retries + 1):
                response = await handler(request)
                if (
                    (observation is not None and observation.started)
                    or _has_visible_ai_content(response)
                    or attempt >= self.max_retries
                ):
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
