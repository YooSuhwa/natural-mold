from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.exception_handlers import register_exception_handlers
from app.models.audit_event import AuditEvent
from app.routers.mcp_apps import router
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
