"""Bounded application retry policy for lease-owned provider sends (internal I12).

Lease-owned provider SDKs run with their own retries disabled, so the only
retry is this application loop in :mod:`app.agent_runtime.lease_bound_model`.
Each attempt runs under its own freshly opened dispatch permit; the permit is
closed before the backoff sleep, and a revoked authority stops the loop at the
next permit instead of issuing another send.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
import httpx
import httpx2
import openai

_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_RETRYABLE_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    httpx.TransportError,
    httpx2.TransportError,
    openai.APIConnectionError,
    anthropic.APIConnectionError,
)


def _status_code(exc: BaseException) -> int | None:
    for candidate in (getattr(exc, "status_code", None), getattr(exc, "code", None)):
        if isinstance(candidate, int):
            return candidate
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


@dataclass(frozen=True, slots=True)
class LeaseProviderRetryPolicy:
    """Attempt budget and exponential backoff for one logical provider call."""

    max_attempts: int = 3
    initial_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay < 0 or self.max_delay < 0 or self.backoff_factor < 1:
            raise ValueError("retry delays must be non-negative and backoff_factor >= 1")

    def delay_for(self, failed_attempt: int) -> float:
        """Backoff after the ``failed_attempt``-th (1-based) attempt."""

        delay = self.initial_delay * self.backoff_factor ** max(0, failed_attempt - 1)
        return min(delay, self.max_delay)

    def should_retry(self, exc: BaseException, failed_attempt: int) -> bool:
        if failed_attempt >= self.max_attempts:
            return False
        return is_retryable_provider_error(exc)


def is_retryable_provider_error(exc: BaseException) -> bool:
    """Transient transport/status failures only; authority denials never retry."""

    if isinstance(exc, _RETRYABLE_TYPES):
        return True
    status = _status_code(exc)
    return status is not None and status in _RETRYABLE_STATUS


NO_APPLICATION_RETRY = LeaseProviderRetryPolicy(max_attempts=1)
