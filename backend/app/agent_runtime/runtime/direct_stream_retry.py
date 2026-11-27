"""Bound opt-in direct provider sends to one budget per model call."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.model_fallback import _sanitize_request_for_fallback
from langchain_core.callbacks import BaseCallbackHandler, BaseCallbackManager
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from app.agent_runtime.lease_provider_retry_policy import is_retryable_provider_error
from app.agent_runtime.runtime.model_response_content import has_visible_ai_content
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.stream_retry_authority import StreamAttemptDenied, check_stream_attempt
from app.agent_runtime.stream_retry_policy import (
    AttemptOutcome,
    PartialModelStreamError,
    gateway_stream_status,
    record_stream_attempt,
    stop_delegation_retry,
    stream_error_retryable,
    stream_retry_enabled,
)


class _OutputBoundary(BaseCallbackHandler):
    """Observe the first model output in either LangGraph streaming protocol."""

    run_inline = True

    def __init__(self) -> None:
        self.started = False

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        self.started = True

    def on_stream_event(self, event: Any, **kwargs: Any) -> None:
        # The v3 chunk bridge emits message-start only after receiving a
        # provider chunk. Even a role-only chunk may already be on the wire.
        self.started = True

    def attach(self, model: BaseChatModel) -> BaseChatModel:
        callbacks = model.callbacks
        if isinstance(callbacks, BaseCallbackManager):
            callbacks = callbacks.copy()
            callbacks.add_handler(self, inherit=False)
        else:
            callbacks = [*(callbacks or []), self]
        # Model instances and their underlying HTTP pools are cached and shared.
        return model.model_copy(update={"callbacks": callbacks})


def direct_stream_retry_for(cfg: AgentConfig) -> bool:
    if cfg.run_path not in {"chat", "subagent"}:
        return False
    return stream_retry_enabled(cfg.provider) or any(
        stream_retry_enabled(entry.get("provider", "")) for entry in cfg.model_fallback_chain or ()
    )


def configured_attempts(middleware_configs: list[dict[str, Any]] | None) -> int:
    for config in middleware_configs or ():
        if config.get("type") == "model_retry":
            limit = (config.get("params") or {}).get("max_retries", 2)
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
                raise ValueError("model_retry max_retries must be a non-negative integer")
            return min(2, limit) + 1
    return 3


def _retryable_direct_error(model: BaseChatModel, exc: Exception) -> bool:
    provider = (model.metadata or {}).get("moldy_provider")
    # Dynamic model-routing middleware may substitute an ordinary SDK client
    # with its own retry budget. Only our single-send candidates may be replayed.
    if not isinstance(provider, str) or not stream_error_retryable(
        exc, is_retryable_provider_error
    ):
        return False
    return stream_retry_enabled(provider) or gateway_stream_status(exc) is None


class DirectStreamRetryMiddleware(AgentMiddleware):
    """Use one provider send per attempt, sharing the budget with fallbacks."""

    def __init__(
        self,
        *,
        fallbacks: list[BaseChatModel],
        max_attempts: int = 3,
        check_authority: Callable[[], Awaitable[None]] = check_stream_attempt,
        initial_delay: float = 1.0,
    ) -> None:
        self.fallbacks = tuple(fallbacks)
        self.max_attempts = min(3, max(1, max_attempts))
        self.check_authority = check_authority
        self.initial_delay = initial_delay

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        try:
            return await self._attempts(request, handler)
        except (Exception, asyncio.CancelledError) as exc:
            # A parent may wrap the entire task() in a tool retry middleware.
            # The child may already have used tools before this model step.
            stop_delegation_retry(exc)
            raise

    async def _admit(self, number: int) -> None:
        if number == 1:
            await self.check_authority()
            return
        previous = number - 1
        # No output was delivered by the previous send. Classify it as retried
        # only after the next send has passed cancellation and ownership checks.
        try:
            await asyncio.sleep(self.initial_delay * 2 ** (previous - 1))
            await self.check_authority()
        except asyncio.CancelledError:
            record_stream_attempt(attempt=previous, output_started=False, outcome="cancelled")
            raise
        except Exception:
            record_stream_attempt(attempt=previous, output_started=False, outcome="denied")
            raise
        record_stream_attempt(attempt=previous, output_started=False, outcome="retry")

    async def _attempts(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        candidates = (request.model, *self.fallbacks)
        candidate_index = 0
        for number in range(1, self.max_attempts + 1):
            candidate = candidates[candidate_index]
            attempt_request = (
                _sanitize_request_for_fallback(request, candidate) if candidate_index else request
            )
            boundary = _OutputBoundary()
            attempt_request = attempt_request.override(model=boundary.attach(candidate))
            await self._admit(number)
            try:
                response = await handler(attempt_request)
            except asyncio.CancelledError:
                record_stream_attempt(
                    attempt=number, output_started=boundary.started, outcome="cancelled"
                )
                raise
            except (StreamAttemptDenied, PartialModelStreamError):
                raise
            except Exception as exc:
                outcome: AttemptOutcome
                if boundary.started:
                    outcome = "partial"
                elif not _retryable_direct_error(candidate, exc):
                    outcome = "unclassified"
                elif number == self.max_attempts:
                    outcome = "exhausted"
                else:
                    candidate_index = min(candidate_index + 1, len(candidates) - 1)
                    continue
                record_stream_attempt(
                    attempt=number, output_started=boundary.started, outcome=outcome
                )
                if outcome == "partial":
                    raise PartialModelStreamError() from exc
                raise
            # Retain one retry of an entirely empty, unstreamed answer. It
            # consumes the same call budget and never replays observable output.
            if (
                number == 1
                and self.max_attempts > 1
                and not boundary.started
                and isinstance((candidate.metadata or {}).get("moldy_provider"), str)
                and not has_visible_ai_content(response)
            ):
                continue
            record_stream_attempt(
                attempt=number, output_started=boundary.started, outcome="success"
            )
            return response
        raise AssertionError("a model call must return or propagate its last error")

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        # Production conversations run asynchronously, where cancellation and
        # ownership can be rechecked. Standalone synchronous callers send once.
        return handler(request)
