"""Narrow recognition and log-based metrics for opt-in gateway stream retries."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

import openai
from langgraph.errors import NodeCancelledError

from app.config import settings

logger = logging.getLogger(__name__)
type AttemptOutcome = Literal[
    "retry", "success", "exhausted", "unclassified", "partial", "cancelled", "denied"
]
type GatewayStatus = Literal[429, 504]


class PartialModelStreamError(RuntimeError):
    """The provider stopped after sending part of a model turn."""

    code = "partial_model_stream"

    def __init__(self) -> None:
        super().__init__(
            "모델 응답이 중간에 끊겼습니다. 받은 내용과 도구 실행 상태를 확인해주세요."
        )


# These are the full messages observed in the gateway fixtures. An arbitrary
# server diagnostic mentioning one of these phrases is not evidence of a retryable frame.
_FRAME_MESSAGES: dict[str, GatewayStatus] = {
    "concurrency limit exceeded for user, please retry later": 429,
    "bad response status code 504": 504,
    "504 gateway time-out": 504,
}


def stream_retry_enabled(provider: str) -> bool:
    return provider in settings.chat_stream_retry_providers


def gateway_stream_status(exc: BaseException) -> GatewayStatus | None:
    """Read the SDK's structured SSE error; leave HTTP/transport errors to their classifier."""
    if type(exc) is not openai.APIError or exc.request.method != "POST":
        return None
    body = exc.body
    if not isinstance(body, dict) or not isinstance(body.get("message"), str):
        return None
    if body["message"] != exc.message:
        return None
    status = _FRAME_MESSAGES.get(exc.message.strip().casefold())
    if status is None:
        return None
    # Some gateways attach the generic "openai_error" type. More specific or
    # contradictory classifications (e.g. permission errors) must win over the text.
    if any(body.get(key) not in (None, status, str(status)) for key in ("code", "status")):
        return None
    if body.get("type") not in (None, "openai_error") or body.get("param") is not None:
        return None
    return status


def stream_error_retryable(exc: Exception, standard: Callable[[Exception], bool]) -> bool:
    """Keep in-band error codes out of the existing transport/status retry policies."""
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code in (401, 403):
            return False
    elif isinstance(exc, openai.APIError) and not isinstance(exc, openai.APIConnectionError):
        return gateway_stream_status(exc) is not None
    return standard(exc)


def record_stream_attempt(*, attempt: int, output_started: bool, outcome: AttemptOutcome) -> None:
    """One countable record per completed provider attempt, with bounded non-content labels."""
    logger.info(
        "model_stream_attempt attempt=%d output_started=%s outcome=%s",
        attempt,
        output_started,
        outcome,
    )


def stop_delegation_retry(error: BaseException) -> None:
    """Preserve the provider error and tell a parent tool wrapper to propagate it."""
    error.__dict__["_moldy_stream_attempt_terminal"] = True


def delegation_retry_stopped(error: Exception) -> bool:
    # LangGraph translates cancellation raised inside a model node and chains
    # the original CancelledError. Its parent task must still propagate the stop.
    if error.__dict__.get("_moldy_stream_attempt_terminal") is True:
        return True
    cause = error.__cause__ if isinstance(error, NodeCancelledError) else None
    return cause is not None and cause.__dict__.get("_moldy_stream_attempt_terminal") is True
