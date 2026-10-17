"""OpenFGA connection health for ``/api/health`` and the operator screen (14.3; H-04).

One probe (``FgaClient.ping``) per ``TTL`` per process, behind a lock so concurrent
health polls share it. ``ok`` under half the client timeout, ``degraded`` when slower,
``down`` when the call fails. Legacy mode never consults OpenFGA: ``disabled``, no
probe, no network call. Each probe logs ``authz_check_latency_ms`` (14.3); a probe that
turns ``down`` logs ``authz_openfga_down`` once per transition.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol

from app.authz.client import open_client
from app.authz.config import AuthzSettings
from app.schemas.admin_authz import FgaHealthView

logger = logging.getLogger(__name__)
TTL = timedelta(seconds=15)


class Pingable(Protocol):
    async def ping(self) -> None: ...


Opener = Callable[[AuthzSettings], AbstractAsyncContextManager[Pingable]]
_lock = asyncio.Lock()
_cached: FgaHealthView | None = None
_last_success: datetime | None = None


def reset() -> None:
    """Forget the cached probe (tests, settings changes)."""
    global _cached, _last_success, _lock
    _cached = _last_success = None
    _lock = asyncio.Lock()


async def _probe(config: AuthzSettings, opener: Opener) -> tuple[bool, float]:
    started = monotonic()
    try:
        async with opener(config) as client:
            await client.ping()
    except Exception:  # noqa: BLE001 - any failure means the store is unreachable
        return False, (monotonic() - started) * 1000
    return True, (monotonic() - started) * 1000


async def fga_health(config: AuthzSettings, *, opener: Opener | None = None) -> FgaHealthView:
    global _cached, _last_success
    if config.authz_mode == "legacy":
        return FgaHealthView(status="disabled")
    async with _lock:
        now = datetime.now(UTC)
        if _cached is not None and _cached.checked_at and now - _cached.checked_at < TTL:
            return _cached
        reached, latency = await _probe(config, opener or open_client)
        logger.info("authz_check_latency_ms", extra={"operation": "ping", "value": latency})
        if reached:
            _last_success = now
            slow = latency > config.openfga_timeout_ms / 2
            status = "degraded" if slow else "ok"
        else:
            status = "down"
            if _cached is None or _cached.status != "down":
                logger.error("authz_openfga_down", extra={"last_success_at": _last_success})
        _cached = FgaHealthView(
            status=status,
            latency_ms=round(latency, 1) if reached else None,
            checked_at=now,
            last_success_at=_last_success,
        )
        return _cached
