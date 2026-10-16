"""Shadow recorder accounting: counters, the rate cap and the request stamp (8.5, 14.3)."""

import time
from collections import Counter
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, fields, replace
from uuid import UUID

_request: ContextVar[tuple[str, str | None]] = ContextVar(
    "moldy_authz_shadow_request", default=("-", None)
)


def bind_shadow_request(route: str, request_id: str | None) -> Token[tuple[str, str | None]]:
    """Route template and request id stamped onto samples submitted in this context."""
    return _request.set((route, request_id))


def current_request() -> tuple[str, str | None]:
    return _request.get()


@dataclass
class ShadowCounters:
    """Log-based metrics (14.3 ``authz_shadow_mismatch``) plus the drop accounting."""

    submitted: int = 0
    rate_limited: int = 0
    dropped: int = 0
    evaluated: int = 0
    matched: int = 0
    mismatches: int = 0
    overlay_vetoed: int = 0
    errors: int = 0
    write_errors: int = 0
    mismatch_by_type: Counter[tuple[UUID, str]] = field(default_factory=Counter)

    def snapshot(self) -> "ShadowCounters":
        """An independent copy for health/metrics readers."""
        return replace(self, mismatch_by_type=Counter(self.mismatch_by_type))

    def summary(self) -> dict[str, int]:
        """Flat totals for the periodic ``authz_shadow_summary`` log line."""
        return {f.name: getattr(self, f.name) for f in fields(self) if f.type is int}


class RateLimit:
    """Token bucket of ``rate`` samples per second; ``rate=0`` admits nothing."""

    def __init__(self, rate: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate, self._clock = rate, clock
        self._tokens, self._at = float(rate), clock()

    def admit(self) -> bool:
        now = self._clock()
        self._tokens = min(self._rate, self._tokens + (now - self._at) * self._rate)
        self._at = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True
