from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import Connection
from app.models.credential import Credential
from app.models.tool import Tool
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_tool(client: AsyncClient, db: AsyncSession):
    # M6: CUSTOM tool 생성은 connection_id 필수 — credential + connection 선행 세팅
    credential = Credential(
        user_id=TEST_USER_ID,
        name="Weather Key",
        credential_type="api_key",
        provider_name="custom",
        data_encrypted=json.dumps({"api_key": "secret"}),
        field_keys=["api_key"],
    )
    db.add(credential)
    await db.flush()
    connection = Connection(
        user_id=TEST_USER_ID,
        type="custom",
        provider_name="custom",
        display_name="Weather",
        credential_id=credential.id,
    )
    db.add(connection)
    await db.commit()

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Weather API",
            "description": "Get weather for a city",
            "api_url": "https://api.weather.com/v1",
            "http_method": "GET",
            "parameters_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "auth_type": "api_key",
            "connection_id": str(connection.id),
        },
    )
    assert resp.status_code == 201
    tool = resp.json()
    assert tool["name"] == "Weather API"
    assert tool["type"] == "custom"
    tool_id = tool["id"]

    # List
    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Delete
    resp = await client.delete(f"/api/tools/{tool_id}")
    assert resp.status_code == 204

    resp = await client.get("/api/tools")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_tool(client: AsyncClient):
    resp = await client.delete("/api/tools/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_custom_tool_requires_connection(client: AsyncClient):
    """M6: CUSTOM tool 생성 시 connection_id 누락 → 422 (pydantic invariant)."""
    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "My API",
            "api_url": "https://example.com",
            "http_method": "GET",
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_custom_tool(
    *, user_id: uuid.UUID | None = None, name: str = "my_custom_tool", **overrides
) -> Tool:
    if user_id is None:
        user_id = TEST_USER_ID
    return Tool(
        user_id=user_id,
        type="custom",
        name=name,
        api_url="https://example.com/api",
        http_method="GET",
        auth_type="api_key",
        **overrides,
    )


def _make_credential(
    *,
    user_id: uuid.UUID | None = None,
    name: str = "Custom Key",
    provider_name: str = "custom",
    data: dict | None = None,
) -> Credential:
    if user_id is None:
        user_id = TEST_USER_ID
    return Credential(
        user_id=user_id,
        name=name,
        credential_type="api_key",
        provider_name=provider_name,
        data_encrypted=json.dumps(data or {"api_key": "secret"}),
    )


# ---------------------------------------------------------------------------
# PATCH /api/tools/{id} — connection_id (M6.1 옵션 D)
# ---------------------------------------------------------------------------


async def _seed_credential_and_connection(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    conn_type: str = "custom",
    provider_name: str = "custom",
    display_name: str = "Test Connection",
) -> Connection:
    cred = Credential(
        user_id=user_id,
        name=f"{display_name} Key",
        credential_type="api_key",
        provider_name=provider_name,
        data_encrypted=json.dumps({"api_key": "secret"}),
        field_keys=["api_key"],
    )
    db.add(cred)
    await db.flush()
    conn = Connection(
        user_id=user_id,
        type=conn_type,
        provider_name=provider_name,
        display_name=display_name,
        credential_id=cred.id,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


@pytest.mark.asyncio
async def test_patch_tool_connection_id_custom_success(client: AsyncClient, db: AsyncSession):
    """CUSTOM tool + CUSTOM connection → 200, connection_id 반영."""
    initial_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Initial"
    )
    new_conn = await _seed_credential_and_connection(db, conn_type="custom", display_name="New")
    tool = _make_custom_tool(connection_id=initial_conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(new_conn.id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connection_id"] == str(new_conn.id)


@pytest.mark.asyncio
async def test_patch_tool_connection_id_mcp_success(client: AsyncClient, db: AsyncSession):
    """MCP tool + MCP connection → 200."""
    mcp_conn = await _seed_credential_and_connection(
        db,
        conn_type="mcp",
        provider_name="custom",
        display_name="MCP Conn",
    )
    tool = Tool(
        user_id=TEST_USER_ID,
        type="mcp",
        name="my_mcp_tool",
        auth_type="api_key",
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(mcp_conn.id)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connection_id"] == str(mcp_conn.id)


@pytest.mark.asyncio
async def test_patch_tool_connection_id_prebuilt_400(client: AsyncClient, db: AsyncSession):
    """PREBUILT system tool → 400 (PREBUILT는 (user_id, provider_name) 스코프)."""
    tool = Tool(
        type="prebuilt",
        is_system=True,
        provider_name="naver",
        name="Naver Blog Search",
        description="네이버 블로그 검색",
        auth_type="api_key",
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    prebuilt_conn = await _seed_credential_and_connection(
        db, conn_type="prebuilt", provider_name="naver", display_name="Naver Conn"
    )

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(prebuilt_conn.id)},
    )
    assert resp.status_code == 400, resp.text
    assert "PREBUILT" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_tool_connection_id_other_user_connection_404(
    client: AsyncClient, db: AsyncSession
):
    """다른 유저의 connection → 404 (IDOR 방지)."""
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    other_conn = await _seed_credential_and_connection(
        db, user_id=other_user_id, conn_type="custom", display_name="Other"
    )
    own_conn = await _seed_credential_and_connection(db, conn_type="custom", display_name="Own")
    tool = _make_custom_tool(connection_id=own_conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(other_conn.id)},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_patch_tool_connection_id_type_mismatch_422(client: AsyncClient, db: AsyncSession):
    """CUSTOM tool + MCP connection → 422 (type 정합성)."""
    own_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Own Custom"
    )
    mcp_conn = await _seed_credential_and_connection(db, conn_type="mcp", display_name="MCP Wrong")
    tool = _make_custom_tool(connection_id=own_conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(mcp_conn.id)},
    )
    assert resp.status_code == 422, resp.text
    assert "does not match" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_tool_connection_id_none_clears_binding(client: AsyncClient, db: AsyncSession):
    """None으로 설정 → connection_id NULL (해제)."""
    conn = await _seed_credential_and_connection(db, conn_type="custom", display_name="ToClear")
    tool = _make_custom_tool(connection_id=conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connection_id"] is None


@pytest.mark.asyncio
async def test_patch_tool_nonexistent_404(client: AsyncClient):
    """존재하지 않는 tool → 404."""
    resp = await client.patch(
        "/api/tools/00000000-0000-0000-0000-000000000999",
        json={"connection_id": None},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_tool_unknown_field_422(client: AsyncClient, db: AsyncSession):
    """`extra="forbid"` — 알 수 없는 필드는 422."""
    conn = await _seed_credential_and_connection(db, conn_type="custom", display_name="Extra")
    tool = _make_custom_tool(connection_id=conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"name": "renamed"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_patch_tool_empty_body_preserves_binding(client: AsyncClient, db: AsyncSession):
    """빈 body 전송은 미전송으로 해석 — 기존 connection_id 유지.

    `connection_id: None`으로 덮어써 실수로 바인딩 해제되는 것을 방지.
    명시적 해제는 `{"connection_id": null}` 전송으로만 가능.
    """
    conn = await _seed_credential_and_connection(db, conn_type="custom", display_name="Preserve")
    tool = _make_custom_tool(connection_id=conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    original_connection_id = tool.connection_id

    resp = await client.patch(f"/api/tools/{tool.id}", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["connection_id"] == str(original_connection_id)

    await db.refresh(tool)
    assert tool.connection_id == original_connection_id


@pytest.mark.asyncio
async def test_patch_tool_other_user_owned_tool_404(client: AsyncClient, db: AsyncSession):
    """다른 유저 소유의 user-tool PATCH → 404 (정보 노출 방지)."""
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    other_conn = await _seed_credential_and_connection(
        db, user_id=other_user_id, conn_type="custom", display_name="Other Conn"
    )
    other_tool = _make_custom_tool(
        user_id=other_user_id, name="strangers_tool", connection_id=other_conn.id
    )
    db.add(other_tool)
    await db.commit()
    await db.refresh(other_tool)

    resp = await client.patch(
        f"/api/tools/{other_tool.id}",
        json={"connection_id": None},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_tool_rejects_disabled_connection(client: AsyncClient, db: AsyncSession):
    """status='disabled' connection으로 rebind → 409 (런타임 invariant 정합)."""
    initial_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Initial"
    )
    disabled_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Disabled"
    )
    disabled_conn.status = "disabled"
    await db.commit()

    tool = _make_custom_tool(connection_id=initial_conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(disabled_conn.id)},
    )
    assert resp.status_code == 409, resp.text
    assert "disabled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_patch_custom_tool_rejects_connection_without_credential(
    client: AsyncClient, db: AsyncSession
):
    """CUSTOM tool은 credential 없는 connection에 bind 불가 — 422.

    (chat_service._gate_connection_credential과 동일 invariant)
    """
    initial_conn = await _seed_credential_and_connection(
        db, conn_type="custom", display_name="Initial"
    )
    # credential 없는 custom connection 생성
    bare_conn = Connection(
        user_id=TEST_USER_ID,
        type="custom",
        provider_name="custom",
        display_name="Bare CUSTOM",
        credential_id=None,
        is_default=False,
        status="active",
    )
    db.add(bare_conn)
    await db.commit()
    await db.refresh(bare_conn)

    tool = _make_custom_tool(connection_id=initial_conn.id)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(bare_conn.id)},
    )
    assert resp.status_code == 422, resp.text
    assert "credential" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_patch_mcp_tool_allows_connection_without_credential(
    client: AsyncClient, db: AsyncSession
):
    """MCP tool은 auth_type='none'이면 credential 없어도 OK (현 버전 v1 스코프)."""
    initial_conn = await _seed_credential_and_connection(
        db, conn_type="mcp", provider_name="initial_mcp", display_name="Initial MCP"
    )
    # extra_config 추가 (MCP 필수)
    initial_conn.extra_config = {"url": "https://x.example.com", "auth_type": "none"}
    bare_conn = Connection(
        user_id=TEST_USER_ID,
        type="mcp",
        provider_name="bare_mcp",
        display_name="Bare MCP",
        credential_id=None,
        extra_config={"url": "https://y.example.com", "auth_type": "none"},
        is_default=False,
        status="active",
    )
    db.add(bare_conn)
    await db.commit()
    await db.refresh(bare_conn)

    tool = Tool(
        user_id=TEST_USER_ID,
        type="mcp",
        name="mcp_no_cred",
        connection_id=initial_conn.id,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    resp = await client.patch(
        f"/api/tools/{tool.id}",
        json={"connection_id": str(bare_conn.id)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connection_id"] == str(bare_conn.id)
