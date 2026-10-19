"""Process-level ``AuthzService`` composition root (plan 8.2, 8.5, 8.6; P3-0).

``authz_runtime(app)`` is entered from the FastAPI lifespan and publishes the
service on ``app.state.authz``. Routes take it through ``Depends(get_authz)``, so
tests swap it with ``app.dependency_overrides[get_authz]``.

* ``legacy`` (default): a service over a client that refuses every call. No
  OpenFGA client is opened and there is no overlay or recorder, so a request
  makes no network call and no extra database query.
* ``shadow``/``enforce``: one long-lived OpenFGA client per process, the D12
  revocation overlay on a platform session factory and, in shadow only, the
  C-06 recorder installed as the shadow hook.

The recorder decides through a hook-less ``AuthzDecisions`` that shares the
client and overlay, which breaks the service/recorder construction cycle.
Shutdown stops the recorder (draining its diffs) before the client is closed.
``main`` opens it with ``open_authz_runtime`` and ``shutdown_runtime_resources``
closes it with ``close_authz_runtime`` after background runs are drained and
before the database is disposed. ``runtime_authz()`` serves non-request code.
"""

import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request

from app.authz.client import CheckQuery, Consistency, ListQuery, open_client
from app.authz.config import AuthzSettings
from app.authz.revocation_overlay import DbRevocationOverlay
from app.authz.service import AuthzService
from app.authz.service_core import AuthzDecisions
from app.authz.service_types import AuthzClient, AuthzNotConsulted, parse_enforce_types
from app.authz.shadow import start_shadow_recorder, stop_shadow_recorder
from app.authz.shadow_metrics import bind_shadow_request
from app.config import settings
from app.database import platform_sessions

logger = logging.getLogger(__name__)
ClientOpener = Callable[[AuthzSettings], AbstractAsyncContextManager[AuthzClient]]


class UnconsultedClient:
    """The legacy-mode client; ``AuthzDecisions`` refuses before ever reaching it."""

    async def check(
        self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> bool:
        raise AuthzNotConsulted("AUTHZ_MODE=legacy does not consult OpenFGA")

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        raise AuthzNotConsulted("AUTHZ_MODE=legacy does not consult OpenFGA")

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        raise AuthzNotConsulted("AUTHZ_MODE=legacy does not consult OpenFGA")


def legacy_service() -> AuthzService:
    """No client, overlay or hook; ``AUTHZ_ENFORCE_TYPES`` is not even parsed."""
    return AuthzService(UnconsultedClient(), mode="legacy")


_LEGACY = legacy_service()
_current: AuthzService | None = None
_stack: AsyncExitStack | None = None


@asynccontextmanager
async def authz_runtime(
    app: FastAPI,
    config: AuthzSettings | None = None,
    *,
    opener: ClientOpener = open_client,
) -> AsyncIterator[AuthzService]:
    """Lifespan owner of the process ``AuthzService`` (published on ``app.state.authz``)."""
    global _current
    config = config or settings
    if config.authz_mode == "legacy":
        app.state.authz = _current = _LEGACY
        try:
            yield _LEGACY
        finally:
            _current = None
        return
    types = parse_enforce_types(config.authz_enforce_types)
    if not (config.openfga_store_id and config.openfga_model_id):
        logger.warning("authz %s without pinned OPENFGA_STORE_ID/MODEL_ID", config.authz_mode)
    async with opener(config) as client:
        overlay = DbRevocationOverlay(platform_sessions("authz.revocation_overlay"))
        decisions = AuthzDecisions(
            client, mode=config.authz_mode, enforce_types=types, overlay=overlay
        )
        recorder = start_shadow_recorder(config, decisions.shadow_decide)
        hook = recorder.submit if recorder is not None else None
        service = AuthzService.from_settings(client, config, shadow_hook=hook, overlay=overlay)
        app.state.authz = _current = service
        try:
            yield service
        finally:
            _current = None
            await stop_shadow_recorder()  # drain before the client below closes


async def open_authz_runtime(
    app: FastAPI, config: AuthzSettings | None = None, *, opener: ClientOpener = open_client
) -> AuthzService:
    """Enter ``authz_runtime`` for the process; ``close_authz_runtime`` exits it."""
    global _stack
    await close_authz_runtime()
    stack = AsyncExitStack()
    service = await stack.enter_async_context(authz_runtime(app, config, opener=opener))
    _stack = stack
    return service


async def close_authz_runtime() -> None:
    """Shutdown step: stop the recorder, then close the client; a no-op when not open."""
    global _stack
    stack, _stack = _stack, None
    if stack is not None:
        await stack.aclose()


def runtime_authz() -> AuthzService:
    """The process service for workers, triggers and queue jobs (same rules as ``get_authz``)."""
    if _current is not None:
        return _current
    if settings.authz_mode == "legacy":  # not started (tests, scripts)
        return _LEGACY
    raise RuntimeError("AuthzService is not running: authz_runtime was not entered")


async def get_authz(request: Request) -> AuthzService:
    """FastAPI dependency; also stamps route template and request id on shadow samples."""
    route = request.scope.get("route")
    bind_shadow_request(
        getattr(route, "path_format", None) or "-", getattr(request.state, "request_id", None)
    )
    service = getattr(request.app.state, "authz", None)
    return service if isinstance(service, AuthzService) else runtime_authz()
