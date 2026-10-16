"""P3-0 composition root: the process AuthzService, its lifespan and ``get_authz``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import Depends, FastAPI

import app.authz.client as client_module
import app.authz.runtime as runtime
import app.authz.shadow as shadow
from app.authz.config import AuthzSettings
from app.authz.revocation_overlay import DbRevocationOverlay
from app.authz.runtime import (
    authz_runtime,
    close_authz_runtime,
    get_authz,
    legacy_service,
    open_authz_runtime,
    runtime_authz,
)
from app.authz.service import AuthzNotConsulted, AuthzService
from app.authz.service_core import AuthzDecisions
from app.authz.shadow_metrics import current_request
from app.main import create_app
from tests.authz.authz_service_testkit import AGENT, RecordingClient, user


def recording_opener(events: list[str], client: RecordingClient):
    @asynccontextmanager
    async def open_client(_config: AuthzSettings) -> AsyncIterator[RecordingClient]:
        events.append("open")
        try:
            yield client
        finally:
            events.append("close")

    return open_client


@pytest.fixture(autouse=True)
async def _no_leftover_recorder() -> AsyncIterator[None]:
    yield
    await close_authz_runtime()
    await shadow.stop_shadow_recorder(0)


@pytest.mark.asyncio
async def test_legacy_opens_no_client_and_never_consults() -> None:
    events: list[str] = []
    config = AuthzSettings(authz_mode="legacy", authz_enforce_types="not_a_type")
    app = FastAPI()
    async with authz_runtime(
        app, config, opener=recording_opener(events, RecordingClient())
    ) as svc:
        assert app.state.authz is svc
        assert svc.mode == "legacy"
        assert svc._overlay is None and svc._shadow_hook is None
        with pytest.raises(AuthzNotConsulted):
            await svc.check(user(), "view", AGENT)
    assert events == []
    assert shadow._recorder is None


@pytest.mark.asyncio
async def test_legacy_default_opener_constructs_no_sdk_client_or_session(monkeypatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("legacy mode must not construct this")

    monkeypatch.setattr(client_module, "OpenFgaClient", forbidden)
    monkeypatch.setattr(runtime, "platform_sessions", forbidden)
    monkeypatch.setattr(runtime, "DbRevocationOverlay", forbidden)
    async with authz_runtime(FastAPI(), AuthzSettings(authz_mode="legacy")) as svc:
        assert svc.mode == "legacy"


@pytest.mark.asyncio
async def test_shadow_wires_recorder_hook_overlay_and_long_lived_client() -> None:
    events: list[str] = []
    client = RecordingClient()
    app = FastAPI()
    config = AuthzSettings(authz_mode="shadow")
    async with authz_runtime(app, config, opener=recording_opener(events, client)) as svc:
        recorder = shadow._recorder
        assert recorder is not None
        assert svc.mode == "shadow" and svc._client is client
        assert svc._shadow_hook == recorder.submit
        assert isinstance(svc._overlay, DbRevocationOverlay)
        decide_owner = recorder._decide.__self__  # type: ignore[attr-defined]
        assert type(decide_owner) is AuthzDecisions
        assert decide_owner._client is client and decide_owner._overlay is svc._overlay
        assert decide_owner._shadow_hook is None  # no service <-> recorder cycle
        assert events == ["open"]
    assert events == ["open", "close"]
    assert shadow._recorder is None


@pytest.mark.asyncio
async def test_enforce_parses_types_and_starts_no_recorder() -> None:
    events: list[str] = []
    app = FastAPI()
    config = AuthzSettings(authz_mode="enforce", authz_enforce_types="agent")
    async with authz_runtime(
        app, config, opener=recording_opener(events, RecordingClient())
    ) as svc:
        assert svc.enforces("agent") and not svc.enforces("organization")
        assert svc._shadow_hook is None
        assert isinstance(svc._overlay, DbRevocationOverlay)
        assert shadow._recorder is None
    assert events == ["open", "close"]


@pytest.mark.asyncio
async def test_enforce_rejects_unknown_types_before_opening_a_client() -> None:
    events: list[str] = []
    config = AuthzSettings(authz_mode="enforce", authz_enforce_types="not_a_type")
    with pytest.raises(ValueError, match="not_a_type"):
        async with authz_runtime(
            FastAPI(), config, opener=recording_opener(events, RecordingClient())
        ):
            pass
    assert events == []


@pytest.mark.asyncio
async def test_shutdown_stops_recorder_before_closing_client_even_on_error(monkeypatch) -> None:
    events: list[str] = []
    real_stop = shadow.stop_shadow_recorder

    async def stop(drain_seconds: float = 5.0) -> None:
        events.append("stop")
        await real_stop(drain_seconds)

    monkeypatch.setattr(runtime, "stop_shadow_recorder", stop)
    config = AuthzSettings(authz_mode="shadow")
    with pytest.raises(RuntimeError, match="body"):
        async with authz_runtime(
            FastAPI(), config, opener=recording_opener(events, RecordingClient())
        ):
            raise RuntimeError("body")
    assert events == ["open", "stop", "close"]


def _probe_app() -> FastAPI:
    app = create_app()

    @app.get("/probe/{item_id}")
    async def probe(item_id: str, authz: AuthzService = Depends(get_authz)) -> dict[str, object]:
        route, request_id = current_request()
        return {"mode": authz.mode, "route": route, "request_id": request_id, "id": id(authz)}

    return app


async def _get(app: FastAPI) -> dict[str, object]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/probe/x1", headers={"X-Request-Id": "rid-1"})
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_get_authz_returns_lifespan_service_and_stamps_route_and_request_id() -> None:
    app = _probe_app()
    async with authz_runtime(app, AuthzSettings(authz_mode="legacy")) as svc:
        body = await _get(app)
    assert body == {
        "mode": "legacy",
        "route": "/probe/{item_id}",
        "request_id": "rid-1",
        "id": id(svc),
    }


@pytest.mark.asyncio
async def test_get_authz_is_overridable_for_tests() -> None:
    app = _probe_app()
    replacement = AuthzService(RecordingClient(), mode="shadow")
    app.dependency_overrides[get_authz] = lambda: replacement
    body = await _get(app)
    assert body["mode"] == "shadow" and body["id"] == id(replacement)


@pytest.mark.asyncio
async def test_get_authz_without_lifespan_is_legacy_only(monkeypatch) -> None:
    app = _probe_app()
    assert (await _get(app))["mode"] == "legacy"
    monkeypatch.setattr(runtime.settings, "authz_mode", "enforce")
    with pytest.raises(RuntimeError, match="not running"):
        await _get(app)


def test_legacy_service_ignores_enforce_configuration() -> None:
    svc = legacy_service()
    assert svc.mode == "legacy" and not svc.enforces("agent")


@pytest.mark.asyncio
async def test_runtime_authz_serves_the_lifespan_service_then_falls_back(monkeypatch) -> None:
    config = AuthzSettings(authz_mode="enforce")
    async with authz_runtime(
        FastAPI(), config, opener=recording_opener([], RecordingClient())
    ) as svc:
        assert runtime_authz() is svc
    assert runtime_authz().mode == "legacy"  # settings default; nothing running
    monkeypatch.setattr(runtime.settings, "authz_mode", "shadow")
    with pytest.raises(RuntimeError, match="not running"):
        runtime_authz()


@pytest.mark.asyncio
async def test_process_open_close_stops_recorder_then_closes_client(monkeypatch) -> None:
    events: list[str] = []
    real_stop = shadow.stop_shadow_recorder

    async def stop(drain_seconds: float = 5.0) -> None:
        events.append("stop")
        await real_stop(drain_seconds)

    monkeypatch.setattr(runtime, "stop_shadow_recorder", stop)
    app = FastAPI()
    config = AuthzSettings(authz_mode="shadow")
    svc = await open_authz_runtime(app, config, opener=recording_opener(events, RecordingClient()))
    assert runtime_authz() is svc and app.state.authz is svc and events == ["open"]
    await close_authz_runtime()
    await close_authz_runtime()  # idempotent shutdown step
    assert events == ["open", "stop", "close"]
    assert shadow._recorder is None
