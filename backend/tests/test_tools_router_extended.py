"""Extended tests for app.routers.tools — auth config, edge cases."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.connection import Connection
from app.models.credential import Credential
from app.models.tool import Tool
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user() -> None:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        await db.commit()


# ---------------------------------------------------------------------------
# GET /api/tools — ToolResponse.provider_name (M3 hotfix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_exposes_provider_name_for_prebuilt(client: AsyncClient):
    """PREBUILT tool should expose `provider_name` in ToolResponse; BUILTIN stays null.

    Regression fence for Pichai's schemas/tool.py:64 hotfix. Without this field the
    frontend ConnectionBindingDialog cannot scope by provider and falls back to
    legacy auth_config writes — the exact ADR-008 §문제 1 regression we are here to
    prevent.
    """
    await _seed_user()

    async with TestSession() as db:
        naver = Tool(
            type="prebuilt",
            is_system=True,
            provider_name="naver",
            name="Naver Blog Search",
            description="네이버 블로그 검색",
        )
        web_search = Tool(
            type="builtin",
            is_system=True,
            provider_name=None,
            name="Web Search",
            description="DuckDuckGo web search (no credentials)",
        )
        db.add_all([naver, web_search])
        await db.commit()

    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    tools = resp.json()

    by_name = {t["name"]: t for t in tools}
    assert "Naver Blog Search" in by_name
    assert "Web Search" in by_name

    assert by_name["Naver Blog Search"]["provider_name"] == "naver"
    assert by_name["Naver Blog Search"]["type"] == "prebuilt"
    assert by_name["Web Search"]["provider_name"] is None
    assert by_name["Web Search"]["type"] == "builtin"


# ---------------------------------------------------------------------------
# POST /api/tools/custom — connection_id binding (M4)
# ---------------------------------------------------------------------------


async def _seed_custom_credential(user_id: uuid.UUID) -> uuid.UUID:
    async with TestSession() as db:
        cred = Credential(
            user_id=user_id,
            name="Custom API Key",
            credential_type="api_key",
            provider_name="custom_api_key",
            data_encrypted="gAAAAA_stub_encrypted",
        )
        db.add(cred)
        await db.commit()
        return cred.id


async def _seed_connection(
    user_id: uuid.UUID, credential_id: uuid.UUID | None, type_: str = "custom"
) -> uuid.UUID:
    async with TestSession() as db:
        conn = Connection(
            user_id=user_id,
            type=type_,
            provider_name="custom_api_key" if type_ == "custom" else "naver",
            display_name=f"{type_} conn",
            credential_id=credential_id,
            status="active",
        )
        db.add(conn)
        await db.commit()
        return conn.id


@pytest.mark.asyncio
async def test_create_custom_tool_with_connection_id_returns_connection_id(
    client: AsyncClient,
):
    """POST /api/tools/custom with a CUSTOM connection_id echoes it in response.

    Regression fence for M4: frontend find-or-create path passes connection_id
    (not credential_id). ToolResponse.connection_id must round-trip so the UI
    can render the bound connection chip.
    """
    await _seed_user()
    cred_id = await _seed_custom_credential(TEST_USER_ID)
    conn_id = await _seed_connection(TEST_USER_ID, cred_id, type_="custom")

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "My Custom Tool",
            "api_url": "https://api.example.com/v1/do",
            "http_method": "POST",
            "connection_id": str(conn_id),
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["connection_id"] == str(conn_id)
    assert data["type"] == "custom"


@pytest.mark.asyncio
async def test_create_custom_tool_rejects_other_user_connection(client: AsyncClient):
    """Binding another user's connection returns 404 (IDOR guard)."""
    await _seed_user()
    other_user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    async with TestSession() as db:
        other = User(id=other_user_id, email="other@test.com", name="Other")
        db.add(other)
        await db.commit()

    cred_id = await _seed_custom_credential(other_user_id)
    foreign_conn_id = await _seed_connection(other_user_id, cred_id, type_="custom")

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Leaky Custom",
            "api_url": "https://api.example.com",
            "connection_id": str(foreign_conn_id),
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_custom_tool_rejects_non_custom_connection_type(
    client: AsyncClient,
):
    """Binding a PREBUILT-type connection to a CUSTOM tool returns 404.

    Type guard: chat_service._resolve_custom_auth is only wired into the CUSTOM
    branch. Allowing a PREBUILT connection on a CUSTOM tool would misroute to
    the PREBUILT resolver at runtime or fall into _resolve_legacy_tool_auth.
    """
    await _seed_user()
    cred_id = await _seed_custom_credential(TEST_USER_ID)
    prebuilt_conn_id = await _seed_connection(
        TEST_USER_ID, cred_id, type_="prebuilt"
    )

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Misrouted Custom",
            "api_url": "https://api.example.com",
            "connection_id": str(prebuilt_conn_id),
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/tools/custom — consistency + fail-closed guards
# (Codex adversarial 3차 [high] + [medium])
# ---------------------------------------------------------------------------


async def _seed_connection_with_status(
    user_id: uuid.UUID,
    credential_id: uuid.UUID | None,
    status: str,
    display_name: str = "test conn",
) -> uuid.UUID:
    async with TestSession() as db:
        conn = Connection(
            user_id=user_id,
            type="custom",
            provider_name="custom_api_key",
            display_name=display_name,
            credential_id=credential_id,
            status=status,
        )
        db.add(conn)
        await db.commit()
        return conn.id


@pytest.mark.asyncio
async def test_create_custom_tool_ignores_unknown_credential_id_field(
    client: AsyncClient,
):
    """M6 이후 `credential_id`는 `ToolCustomCreate` 스키마에 더 이상 존재하지
    않는다. 클라이언트가 legacy 필드를 보내도 pydantic 이 조용히 무시하고
    connection_id 경유로 생성에 성공해야 한다 (forward-compat).
    """
    await _seed_user()
    cred_in_conn = await _seed_custom_credential(TEST_USER_ID)
    conn_id = await _seed_connection(TEST_USER_ID, cred_in_conn, type_="custom")

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Consistent Tool",
            "api_url": "https://api.example.com",
            "credential_id": str(uuid.uuid4()),  # legacy — 무시되어야 함
            "connection_id": str(conn_id),
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["connection_id"] == str(conn_id)
    assert "credential_id" not in data


@pytest.mark.asyncio
async def test_create_custom_tool_rejects_disabled_connection(
    client: AsyncClient,
):
    """disabled connection에 새 tool을 바인딩하면 409 — runtime에서 fail-closed로
    어차피 막히지만 creation time에 latent failure 차단
    (Codex adversarial 3차 [medium]).
    """
    await _seed_user()
    cred_id = await _seed_custom_credential(TEST_USER_ID)
    disabled_conn_id = await _seed_connection_with_status(
        TEST_USER_ID, cred_id, status="disabled", display_name="disabled"
    )

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Dead-on-arrival",
            "api_url": "https://api.example.com",
            "connection_id": str(disabled_conn_id),
        },
    )
    assert resp.status_code == 409
    detail = resp.json().get("detail", {})
    if isinstance(detail, dict):
        assert detail.get("error_code") == "CUSTOM_CONNECTION_DISABLED"


@pytest.mark.asyncio
async def test_create_custom_tool_rejects_connection_without_credential(
    client: AsyncClient,
):
    """credential이 바인딩되지 않은 connection에 tool을 바인딩하면 400
    (Codex adversarial 3차 [medium]). runtime `_resolve_custom_auth` State 5b
    `ToolConfigError` 로 막히지만 creation time에 차단.
    """
    await _seed_user()
    unbound_conn_id = await _seed_connection_with_status(
        TEST_USER_ID, None, status="active", display_name="unbound"
    )

    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "Unbound Tool",
            "api_url": "https://api.example.com",
            "connection_id": str(unbound_conn_id),
        },
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail", {})
    if isinstance(detail, dict):
        assert detail.get("error_code") == "CUSTOM_CONNECTION_UNBOUND"
