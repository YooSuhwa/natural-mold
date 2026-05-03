"""Health check service + router — model/MCP probes, status mapping, history."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.credentials import service as credential_service
from app.models.health_check_history import HealthCheckHistory
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.user import User
from app.services import health_check as health_check_service
from app.services.model_test import ErrorKind, ModelTestError, ModelTestResult
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_and_model() -> Model:
    async with TestSession() as db:
        db.add(User(id=TEST_USER_ID, email="h@test", name="h"))
        await db.flush()
        cred = await credential_service.create(
            db,
            user_id=TEST_USER_ID,
            definition_key="openai",
            name="model cred",
            data={"api_key": "sk-test"},
        )
        model = Model(
            provider="openai",
            model_name="gpt-4o",
            display_name="GPT-4o",
            # Bind the credential at the model level so the row [Check] flow
            # (and ``check_now`` without explicit credential_id) resolves it.
            default_credential_id=cred.id,
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)
        # Also bind through an Agent so the sweep picks it up.
        from app.models.agent import Agent

        agent = Agent(
            user_id=TEST_USER_ID,
            name="health agent",
            system_prompt="ok",
            model_id=model.id,
            llm_credential_id=cred.id,
        )
        db.add(agent)
        await db.commit()
        return model


async def _seed_mcp_server(*, transport: str = "streamable_http") -> McpServer:
    async with TestSession() as db:
        existing = (
            await db.execute(select(User).where(User.id == TEST_USER_ID))
        ).scalar_one_or_none()
        if existing is None:
            db.add(User(id=TEST_USER_ID, email="h@test", name="h"))
            await db.flush()
        server = McpServer(
            user_id=TEST_USER_ID,
            name="example",
            transport=transport,
            url="https://example.com/mcp" if transport != "stdio" else None,
            command="echo" if transport == "stdio" else None,
            args=[],
            env_vars={},
            headers={},
            credential_id=None,
            status="unknown",
        )
        db.add(server)
        await db.commit()
        await db.refresh(server)
        return server


def _stub_model_test(
    success: bool, *, kind: ErrorKind = "auth"
) -> ModelTestResult:
    if success:
        return ModelTestResult(
            success=True,
            response="pong",
            latency_ms=120,
            tokens_in=5,
            tokens_out=1,
        )
    return ModelTestResult(
        success=False,
        latency_ms=88,
        error=ModelTestError(kind=kind, message="invalid api key", raw=None),
    )


# ---------------------------------------------------------------------------
# Service-layer scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_model_success_writes_healthy_history() -> None:
    model = await _seed_user_and_model()

    async def _fake_test(**kwargs):
        return _stub_model_test(success=True)

    with patch("app.services.health_check.model_test.run_model_test", _fake_test):
        async with TestSession() as db:
            cred = (
                await db.execute(select(credential_service.Credential))
            ).scalars().first()
            history = await health_check_service.check_model(
                db, model=model, credential=cred
            )
            await db.commit()
            assert history.status == "healthy"
            assert history.latency_ms == 120
            assert history.error_kind is None
            rows = (
                await db.execute(
                    select(HealthCheckHistory).where(
                        HealthCheckHistory.target_id == model.id
                    )
                )
            ).scalars().all()
            assert len(rows) == 1


@pytest.mark.asyncio
async def test_check_model_failure_classifies_auth_error() -> None:
    model = await _seed_user_and_model()

    async def _fake_test(**kwargs):
        return _stub_model_test(success=False, kind="auth")

    with patch("app.services.health_check.model_test.run_model_test", _fake_test):
        async with TestSession() as db:
            cred = (
                await db.execute(select(credential_service.Credential))
            ).scalars().first()
            history = await health_check_service.check_model(
                db, model=model, credential=cred
            )
            await db.commit()
            assert history.status == "unhealthy"
            assert history.error_kind == "auth"
            assert "invalid api key" in (history.error_message or "")


@pytest.mark.asyncio
async def test_check_mcp_server_success_updates_server_status() -> None:
    server = await _seed_mcp_server()

    async def _fake_probe(**kwargs):
        return {
            "success": True,
            "server_info": {"name": "fake", "version": "1"},
            "tools": [{"name": "echo"}],
            "error": None,
        }

    with patch("app.services.health_check.mcp_client.connect_and_list", _fake_probe):
        async with TestSession() as db:
            fetched = await db.get(McpServer, server.id)
            assert fetched is not None
            history = await health_check_service.check_mcp_server(
                db, server=fetched, credential=None
            )
            await db.commit()
            refreshed = await db.get(McpServer, server.id)
            assert refreshed is not None
            assert history.status == "healthy"
            assert refreshed.status == "connected"
            assert refreshed.last_tool_count == 1


@pytest.mark.asyncio
async def test_check_mcp_server_auth_error_marks_degraded() -> None:
    server = await _seed_mcp_server()

    async def _fake_probe(**kwargs):
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": "401 Unauthorized: token expired",
        }

    with patch("app.services.health_check.mcp_client.connect_and_list", _fake_probe):
        async with TestSession() as db:
            fetched = await db.get(McpServer, server.id)
            assert fetched is not None
            history = await health_check_service.check_mcp_server(
                db, server=fetched, credential=None
            )
            await db.commit()
            assert history.status == "degraded"
            assert history.error_kind == "auth"
            refreshed = await db.get(McpServer, server.id)
            assert refreshed is not None
            assert refreshed.status == "auth_needed"


@pytest.mark.asyncio
async def test_check_mcp_server_unreachable_marks_unhealthy() -> None:
    server = await _seed_mcp_server()

    async def _fake_probe(**kwargs):
        return {
            "success": False,
            "server_info": {},
            "tools": [],
            "error": "connection timed out",
        }

    with patch("app.services.health_check.mcp_client.connect_and_list", _fake_probe):
        async with TestSession() as db:
            fetched = await db.get(McpServer, server.id)
            assert fetched is not None
            history = await health_check_service.check_mcp_server(
                db, server=fetched, credential=None
            )
            await db.commit()
            assert history.status == "unhealthy"
            assert history.error_kind == "timeout"
            refreshed = await db.get(McpServer, server.id)
            assert refreshed is not None
            assert refreshed.status == "unreachable"


@pytest.mark.asyncio
async def test_check_all_active_returns_counters() -> None:
    await _seed_user_and_model()
    await _seed_mcp_server()

    async def _fake_test(**kwargs):
        return _stub_model_test(success=True)

    async def _fake_probe(**kwargs):
        return {"success": True, "server_info": {}, "tools": [], "error": None}

    with (
        patch("app.services.health_check.model_test.run_model_test", _fake_test),
        patch("app.services.health_check.mcp_client.connect_and_list", _fake_probe),
    ):
        async with TestSession() as db:
            counters = await health_check_service.check_all_active(db)

    assert counters["models_checked"] == 1
    assert counters["mcp_servers_checked"] == 1
    assert counters["healthy"] >= 2


# ---------------------------------------------------------------------------
# Router scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_endpoint_returns_recent_rows(client: AsyncClient) -> None:
    model = await _seed_user_and_model()
    async with TestSession() as db:
        for _ in range(3):
            await health_check_service._record(  # type: ignore[attr-defined]
                db,
                target_kind="model",
                target_id=model.id,
                status="healthy",
                latency_ms=100,
                error_kind=None,
                error_message=None,
                raw=None,
            )
        await db.commit()

    resp = await client.get(
        "/api/health/history",
        params={"target_kind": "model", "target_id": str(model.id), "limit": 10},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["target_id"] == str(model.id)
    assert body[0]["status"] == "healthy"


@pytest.mark.asyncio
async def test_models_summary_returns_latest_per_model(client: AsyncClient) -> None:
    model = await _seed_user_and_model()
    async with TestSession() as db:
        await health_check_service._record(  # type: ignore[attr-defined]
            db,
            target_kind="model",
            target_id=model.id,
            status="healthy",
            latency_ms=80,
            error_kind=None,
            error_message=None,
            raw=None,
        )
        await db.commit()

    resp = await client.get("/api/health/models")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["target_id"] == str(model.id)
    assert body[0]["status"] == "healthy"


@pytest.mark.asyncio
async def test_check_now_endpoint_writes_history_row(client: AsyncClient) -> None:
    model = await _seed_user_and_model()

    async def _fake_test(**kwargs):
        return _stub_model_test(success=True)

    with patch("app.services.health_check.model_test.run_model_test", _fake_test):
        resp = await client.post(
            "/api/health/check",
            params={"target_kind": "model", "target_id": str(model.id)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["target_id"] == str(model.id)


@pytest.mark.asyncio
async def test_check_now_returns_404_for_unknown_model(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/health/check",
        params={"target_kind": "model", "target_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
