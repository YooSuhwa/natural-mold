from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.exception_handlers import register_exception_handlers
from app.models.audit_event import AuditEvent
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.routers.mcp_apps import router
from app.services import mcp_apps_service
from tests.conftest import TEST_USER_ID
from tests.test_mcp_apps_service import _seed


def _app_for_session(db: AsyncSession, *, bypass_csrf: bool) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def _db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    async def _user() -> CurrentUser:
        return CurrentUser(
            id=TEST_USER_ID,
            email="mcp-app@test.local",
            name="MCP App User",
        )

    async def _csrf() -> None:
        return None

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    if bypass_csrf:
        app.dependency_overrides[verify_csrf] = _csrf
    return app


@pytest.mark.asyncio
async def test_widget_restores_private_result_without_replaying_call_or_refreshing_credentials(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(db)
    binding = await db.get(McpAppInvocationBinding, seeded.binding_id)
    assert binding is not None
    binding.structured_content = {"temperature": 23, "note": "server-secret", "api_key": "private"}
    binding.result_meta = {"viewUUID": "view-1", "access_token": "private"}
    await db.commit()
    endpoints = [AsyncMock(), AsyncMock(), AsyncMock()]
    for name, endpoint in zip(
        ["resolve_mcp_auth", "call_mcp_tool_once", "read_mcp_resource_once"], endpoints, strict=True
    ):
        monkeypatch.setattr(mcp_apps_service, name, endpoint)

    url = f"/api/conversations/{seeded.conversation.id}/runs/{seeded.run.id}/mcp-apps/tool-call-1"
    app = _app_for_session(db, bypass_csrf=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        restored = await client.get(url, params={"binding_id": str(seeded.binding_id)})
        assert restored.status_code == 200
        assert restored.headers["cache-control"] == "private, no-store"
        assert restored.json()["mcp_app"] == {
            "version": 1,
            "binding_id": str(seeded.binding_id),
            "run_id": str(seeded.run.id),
            "resource_uri": "ui://weather/dashboard",
            "tool_meta": {"ui": {"resourceUri": "ui://weather/dashboard"}},
            "result_meta": {"viewUUID": "view-1"},
        }
        assert set(restored.json()) == {"mcp_app", "structured_content"}
        content = restored.json()["structured_content"]
        assert content["temperature"] == 23
        assert content["note"] != "server-secret"
        assert "api_key" not in content
        assert "server-secret" not in restored.text

        wrong_binding = await client.get(url, params={"binding_id": str(uuid4())})
        for wrong_url in (
            url.replace("tool-call-1", "other-call"),
            url.replace(str(seeded.run.id), str(uuid4())),
            url.replace(str(seeded.conversation.id), str(uuid4())),
        ):
            missing = await client.get(wrong_url, params={"binding_id": str(seeded.binding_id)})
            assert wrong_binding.status_code == missing.status_code == 404
            assert wrong_binding.json() == missing.json()

        seeded.origin.enabled = False
        await db.commit()
        revoked = await client.get(url, params={"binding_id": str(seeded.binding_id)})
        assert revoked.status_code == 403
        assert revoked.headers["cache-control"] == "private, no-store"
        assert "view-1" not in revoked.text
        assert "temperature" not in revoked.text

    assert (
        await db.scalar(select(AuditEvent).where(AuditEvent.target_id == str(binding.id))) is None
    )
    for endpoint in endpoints:
        endpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_widget_and_proxy_support_opaque_tool_call_ids(db: AsyncSession) -> None:
    seeded = await _seed(db)
    binding = await db.get(McpAppInvocationBinding, seeded.binding_id)
    assert binding is not None
    binding.tool_call_id = "call/route:1"
    await db.commit()
    encoded = quote(binding.tool_call_id, safe="")
    url = f"/api/conversations/{seeded.conversation.id}/runs/{seeded.run.id}/mcp-apps/{encoded}"
    app = _app_for_session(db, bypass_csrf=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        restored = await client.get(url, params={"binding_id": str(seeded.binding_id)})
        assert restored.status_code == 200
        assert restored.json()["mcp_app"]["binding_id"] == str(seeded.binding_id)
        listed = await client.post(url, json={"method": "resources/list", "params": {}})
        assert listed.status_code == 200
        assert [item["uri"] for item in listed.json()["resources"]] == ["ui://weather/dashboard"]


@pytest.mark.asyncio
async def test_mcp_apps_remote_host_returns_direct_bound_resource_list(
    db: AsyncSession,
) -> None:
    seeded = await _seed(db)
    app = _app_for_session(db, bypass_csrf=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/conversations/{seeded.conversation.id}/runs/{seeded.run.id}"
            "/mcp-apps/tool-call-1",
            json={"method": "resources/list", "params": {}},
        )

    assert response.status_code == 200
    assert response.json() == {
        "resources": [
            {
                "uri": "ui://weather/dashboard",
                "name": "weather",
                "mimeType": "text/html;profile=mcp-app",
                "_meta": {"ui": {"resourceUri": "ui://weather/dashboard"}},
            }
        ]
    }
    audit = await db.scalar(select(AuditEvent).where(AuditEvent.action == "mcp_app.resources.list"))
    assert audit is not None
    assert audit.target_id == str(seeded.binding_id)


@pytest.mark.asyncio
async def test_mcp_apps_remote_host_rejects_forged_server_and_resource_ids(
    db: AsyncSession,
) -> None:
    seeded = await _seed(db)
    app = _app_for_session(db, bypass_csrf=True)
    url = f"/api/conversations/{seeded.conversation.id}/runs/{seeded.run.id}/mcp-apps/tool-call-1"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        forged_server = await client.post(
            url,
            json={
                "method": "resources/list",
                "params": {"serverId": "11111111-1111-1111-1111-111111111111"},
            },
        )
        forged_resource = await client.post(
            url,
            json={
                "method": "mcp-apps/read-resource",
                "params": {"uri": "ui://other/admin"},
            },
        )

    assert forged_server.status_code == 403
    assert forged_server.json()["error"]["code"] == "mcp_app_server_mismatch"
    assert forged_resource.status_code == 403
    assert forged_resource.json()["error"]["code"] == "mcp_app_resource_mismatch"


@pytest.mark.asyncio
async def test_mcp_apps_remote_host_requires_csrf_and_rejects_unknown_methods(
    db: AsyncSession,
) -> None:
    seeded = await _seed(db)
    url = f"/api/conversations/{seeded.conversation.id}/runs/{seeded.run.id}/mcp-apps/tool-call-1"
    csrf_app = _app_for_session(db, bypass_csrf=False)
    async with AsyncClient(transport=ASGITransport(app=csrf_app), base_url="http://test") as client:
        csrf = await client.post(url, json={"method": "resources/list", "params": {}})

    validation_app = _app_for_session(db, bypass_csrf=True)
    async with AsyncClient(
        transport=ASGITransport(app=validation_app), base_url="http://test"
    ) as client:
        malformed = await client.post(
            url,
            json={"method": "filesystem/read", "params": {"path": "/etc/passwd"}},
        )

    assert csrf.status_code == 403
    assert csrf.json()["error"]["code"] == "csrf_mismatch"
    assert malformed.status_code == 422
