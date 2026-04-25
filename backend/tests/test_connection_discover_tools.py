"""POST /api/connections/{id}/discover-tools — MCP 서버 tool discovery (M6.1 M7).

mcp_probe(httpx)는 외부 호출이므로 unittest.mock.patch로 차단하고 6 시나리오 검증.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import Connection
from app.models.tool import Tool
from app.models.user import User
from tests.conftest import TEST_USER_ID

OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000fe")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _seed_mcp_connection(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    name: str = "Test MCP",
    url: str = "https://mcp.example.com/mcp",
) -> Connection:
    conn = Connection(
        user_id=user_id,
        type="mcp",
        provider_name="test_mcp",
        display_name=name,
        credential_id=None,
        extra_config={"url": url, "auth_type": "none"},
        is_default=False,
        status="active",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


async def _seed_custom_connection(
    db: AsyncSession, *, user_id: uuid.UUID = TEST_USER_ID
) -> Connection:
    conn = Connection(
        user_id=user_id,
        type="custom",
        provider_name="custom_api_key",
        display_name="Custom",
        credential_id=None,
        extra_config=None,
        is_default=False,
        status="active",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


@pytest.mark.asyncio
async def test_discover_creates_tools_and_returns_items(
    client: AsyncClient, db: AsyncSession
):
    conn = await _seed_mcp_connection(db)

    fake_result = {
        "success": True,
        "server_info": {"name": "test-server", "version": "1.0"},
        "tools": [
            {"name": "search", "description": "web search", "inputSchema": {"type": "object"}},
            {"name": "fetch", "description": "fetch url", "inputSchema": {}},
        ],
    }

    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new=AsyncMock(return_value=fake_result),
    ):
        resp = await client.post(f"/api/connections/{conn.id}/discover-tools")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connection_id"] == str(conn.id)
    assert body["server_info"] == {"name": "test-server", "version": "1.0"}
    assert len(body["items"]) == 2
    names = {item["tool"]["name"] for item in body["items"]}
    assert names == {"search", "fetch"}
    for item in body["items"]:
        assert item["status"] == "created"
        assert item["tool"]["type"] == "mcp"
        assert item["tool"]["connection_id"] == str(conn.id)

    # DB 실측: Tool 레코드 2개 생성되었고 user/connection/type 정확히 설정
    rows = (
        await db.execute(
            select(Tool).where(
                Tool.connection_id == conn.id, Tool.user_id == TEST_USER_ID
            )
        )
    ).scalars().all()
    assert len(rows) == 2
    assert {r.name for r in rows} == {"search", "fetch"}
    assert all(r.type == "mcp" for r in rows)


@pytest.mark.asyncio
async def test_discover_skips_existing_tools_idempotent(
    client: AsyncClient, db: AsyncSession
):
    conn = await _seed_mcp_connection(db)
    fake_result = {
        "success": True,
        "server_info": {},
        "tools": [
            {"name": "search", "description": "v1", "inputSchema": {}},
            {"name": "fetch", "description": "v1", "inputSchema": {}},
        ],
    }

    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new=AsyncMock(return_value=fake_result),
    ):
        first = await client.post(f"/api/connections/{conn.id}/discover-tools")
        second = await client.post(f"/api/connections/{conn.id}/discover-tools")

    assert first.status_code == 200
    assert second.status_code == 200
    assert {i["status"] for i in first.json()["items"]} == {"created"}
    assert {i["status"] for i in second.json()["items"]} == {"existing"}

    rows = (
        await db.execute(
            select(Tool).where(Tool.connection_id == conn.id)
        )
    ).scalars().all()
    assert len(rows) == 2  # 중복 생성 없음


@pytest.mark.asyncio
async def test_discover_other_user_connection_404(
    client: AsyncClient, db: AsyncSession
):
    # 타 유저 생성
    other = User(id=OTHER_USER_ID, email="other@example.com", name="other")
    db.add(other)
    await db.commit()
    conn = await _seed_mcp_connection(db, user_id=OTHER_USER_ID)

    resp = await client.post(f"/api/connections/{conn.id}/discover-tools")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_discover_nonexistent_connection_404(client: AsyncClient):
    fake_id = uuid.uuid4()
    resp = await client.post(f"/api/connections/{fake_id}/discover-tools")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_discover_non_mcp_connection_422(
    client: AsyncClient, db: AsyncSession
):
    conn = await _seed_custom_connection(db)
    resp = await client.post(f"/api/connections/{conn.id}/discover-tools")
    assert resp.status_code == 422
    assert "does not support tool discovery" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_discover_missing_url_422(
    client: AsyncClient, db: AsyncSession
):
    conn = Connection(
        user_id=TEST_USER_ID,
        type="mcp",
        provider_name="broken_mcp",
        display_name="No URL",
        credential_id=None,
        extra_config={"auth_type": "none"},  # url 없음
        is_default=False,
        status="active",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)

    resp = await client.post(f"/api/connections/{conn.id}/discover-tools")
    assert resp.status_code == 422
    assert "extra_config.url" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_discover_probe_failure_502(
    client: AsyncClient, db: AsyncSession
):
    conn = await _seed_mcp_connection(db)
    fake_result = {"success": False, "error": "Connection timeout", "tools": []}

    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new=AsyncMock(return_value=fake_result),
    ):
        resp = await client.post(f"/api/connections/{conn.id}/discover-tools")

    assert resp.status_code == 502
    assert "Connection timeout" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_discover_ignores_malformed_tools(
    client: AsyncClient, db: AsyncSession
):
    conn = await _seed_mcp_connection(db)
    fake_result = {
        "success": True,
        "server_info": {},
        "tools": [
            {"name": "valid", "description": "ok"},
            {"name": ""},  # 빈 이름
            {"description": "no name"},  # name 누락
            "not-a-dict",  # dict 아님
        ],
    }

    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new=AsyncMock(return_value=fake_result),
    ):
        resp = await client.post(f"/api/connections/{conn.id}/discover-tools")

    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    assert resp.json()["items"][0]["tool"]["name"] == "valid"
