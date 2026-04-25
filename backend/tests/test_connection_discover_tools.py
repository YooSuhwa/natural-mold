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
async def test_discover_disabled_connection_409(
    client: AsyncClient, db: AsyncSession
):
    """kill-switch: disabled connection에서 probe/생성 모두 거부."""
    conn = await _seed_mcp_connection(db)
    conn.status = "disabled"
    await db.commit()

    # mcp_probe이 호출되지 않아야 하므로 mock 없이도 509가 나와야 함
    resp = await client.post(f"/api/connections/{conn.id}/discover-tools")
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_discover_race_duplicate_blocked_by_unique_index(
    client: AsyncClient, db: AsyncSession
):
    """동시 두 요청 시뮬레이션: 첫 요청이 insert하기 전에 같은 name을 가진
    Tool row를 미리 박아두어 두 번째 요청이 IntegrityError를 만나도록.

    m14 partial unique index `(user_id, connection_id, name) WHERE type='mcp'`가
    중복을 거부하고, 서비스 코드는 IntegrityError를 catch해 winner row로 흡수.
    최종 응답은 모든 name이 created 또는 existing 중 정확히 한 분류 — 중복 행 없음.
    """
    conn = await _seed_mcp_connection(db)
    fake_result = {
        "success": True,
        "server_info": {},
        "tools": [
            {"name": "race_target", "description": "discovery"},
            {"name": "fresh", "description": "new"},
        ],
    }

    # 동시성 시뮬레이션: 첫 요청 직후 race_target row를 외부에서 박아두는 효과.
    # 실제로는 sleep 사이 다른 요청이 먼저 commit하는 시나리오 — 여기서는 mock
    # 호출 직전에 row를 직접 db.add로 삽입.
    async def insert_race_winner(*args, **kwargs):
        race_tool = Tool(
            user_id=TEST_USER_ID,
            type="mcp",
            name="race_target",
            description="winner",
            connection_id=conn.id,
        )
        db.add(race_tool)
        await db.commit()
        return fake_result

    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new=AsyncMock(side_effect=insert_race_winner),
    ):
        resp = await client.post(f"/api/connections/{conn.id}/discover-tools")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    statuses = {i["tool"]["name"]: i["status"] for i in items}
    # race_target은 외부 winner가 먼저 commit → IntegrityError → existing
    # fresh는 정상 created
    assert statuses.get("race_target") == "existing"
    assert statuses.get("fresh") == "created"

    # DB 실측: race_target은 단 1행만 존재 (m14 unique 보호)
    rows = (
        await db.execute(
            select(Tool).where(
                Tool.connection_id == conn.id,
                Tool.name == "race_target",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_discover_race_preserves_earlier_created_rows(
    client: AsyncClient, db: AsyncSession
):
    """savepoint 격리 검증 — race 발생해도 이미 created된 row는 손실되지 않음.

    이전 구현은 IntegrityError 시 session-wide rollback으로 같은 호출의 이전
    inserts까지 모두 잃었음 (silent catalog drift). begin_nested() savepoint로
    수정 후, 한 row가 race에 걸려도 앞서 commit된 row는 그대로 유지.
    """
    conn = await _seed_mcp_connection(db)
    fake_result = {
        "success": True,
        "server_info": {},
        "tools": [
            {"name": "first", "description": "OK"},
            {"name": "race_target", "description": "winner외부에서먼저"},
            {"name": "third", "description": "after race"},
        ],
    }

    async def insert_race_winner(*args, **kwargs):
        race_tool = Tool(
            user_id=TEST_USER_ID,
            type="mcp",
            name="race_target",
            description="winner",
            connection_id=conn.id,
        )
        db.add(race_tool)
        await db.commit()
        return fake_result

    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new=AsyncMock(side_effect=insert_race_winner),
    ):
        resp = await client.post(f"/api/connections/{conn.id}/discover-tools")

    assert resp.status_code == 200, resp.text
    statuses = {i["tool"]["name"]: i["status"] for i in resp.json()["items"]}
    # race_target은 외부 winner가 먼저 commit → existing
    assert statuses.get("race_target") == "existing"
    # first/third는 race 무관하게 created로 보존되어야 함 (savepoint 격리 효과)
    assert statuses.get("first") == "created"
    assert statuses.get("third") == "created"

    # DB 실측 — first/third가 실제로 commit됐는지
    rows = (
        await db.execute(
            select(Tool).where(
                Tool.connection_id == conn.id,
                Tool.name.in_(["first", "third"]),
            )
        )
    ).scalars().all()
    assert {r.name for r in rows} == {"first", "third"}


@pytest.mark.asyncio
async def test_discover_passes_extra_config_headers_to_probe(
    client: AsyncClient, db: AsyncSession
):
    """extra_config.headers (transport headers)는 probe에도 전달되어야 한다.

    chat runtime의 MCP 빌더가 동일 헤더를 사용하므로, discovery만 헤더 없이 호출하면
    인증 MCP 서버에서 401/잘못된 카탈로그가 반환된다.
    """
    conn = Connection(
        user_id=TEST_USER_ID,
        type="mcp",
        provider_name="auth_mcp",
        display_name="Auth MCP",
        credential_id=None,
        extra_config={
            "url": "https://mcp.example.com",
            "auth_type": "none",
            "headers": {"X-Tenant": "acme"},
        },
        is_default=False,
        status="active",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)

    captured: dict[str, object] = {}

    async def capture(url, auth_config=None, extra_headers=None):
        captured["url"] = url
        captured["auth"] = auth_config
        captured["extra_headers"] = extra_headers
        return {"success": True, "server_info": {}, "tools": []}

    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new=AsyncMock(side_effect=capture),
    ):
        resp = await client.post(f"/api/connections/{conn.id}/discover-tools")

    assert resp.status_code == 200, resp.text
    assert captured["extra_headers"] == {"X-Tenant": "acme"}


@pytest.mark.asyncio
async def test_discover_ignores_oversized_name(
    client: AsyncClient, db: AsyncSession
):
    """Tool.name 컬럼은 String(100) — 원격 서버가 oversized name을 보내면 DataError로
    500이 떨어진다. 검증 단계에서 skip해 controlled 응답 유지.
    """
    conn = await _seed_mcp_connection(db)
    fake_result = {
        "success": True,
        "server_info": {},
        "tools": [
            {"name": "ok_short", "description": "fine"},
            {"name": "x" * 101, "description": "oversize"},
            {"name": "x" * 200, "description": "very oversize"},
        ],
    }
    with patch(
        "app.agent_runtime.mcp_client.test_mcp_connection",
        new=AsyncMock(return_value=fake_result),
    ):
        resp = await client.post(f"/api/connections/{conn.id}/discover-tools")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["tool"]["name"] == "ok_short"


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
