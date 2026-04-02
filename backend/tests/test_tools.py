from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.schemas.tool import _check_server_key_available

# ---------------------------------------------------------------------------
# _check_server_key_available unit tests
# ---------------------------------------------------------------------------


def _patch_settings(**overrides):
    """Patch app.config.settings attributes for testing."""
    defaults = {
        "naver_client_id": "",
        "naver_client_secret": "",
        "google_api_key": "",
        "google_cse_id": "",
        "google_chat_webhook_url": "",
        "google_oauth_client_id": "",
        "google_oauth_client_secret": "",
        "google_oauth_refresh_token": "",
    }
    defaults.update(overrides)
    return patch.multiple("app.config.settings", **defaults)


class TestServerKeyAvailable:
    def test_naver_tool_with_keys(self):
        with _patch_settings(naver_client_id="id123", naver_client_secret="secret456"):
            assert _check_server_key_available("Naver Blog Search") is True
            assert _check_server_key_available("Naver News Search") is True

    def test_naver_tool_without_keys(self):
        with _patch_settings():
            assert _check_server_key_available("Naver Blog Search") is False

    def test_naver_tool_partial_keys(self):
        with _patch_settings(naver_client_id="id123"):
            assert _check_server_key_available("Naver Blog Search") is False

    def test_google_search_with_keys(self):
        with _patch_settings(google_api_key="key123", google_cse_id="cse456"):
            assert _check_server_key_available("Google Search") is True
            assert _check_server_key_available("Google News Search") is True

    def test_google_search_without_keys(self):
        with _patch_settings():
            assert _check_server_key_available("Google Search") is False

    def test_google_chat_with_webhook(self):
        with _patch_settings(google_chat_webhook_url="https://chat.googleapis.com/v1/spaces/xxx"):
            assert _check_server_key_available("Google Chat Send") is True

    def test_google_chat_without_webhook(self):
        with _patch_settings():
            assert _check_server_key_available("Google Chat Send") is False

    def test_gmail_with_oauth(self):
        with _patch_settings(
            google_oauth_client_id="cid",
            google_oauth_client_secret="csecret",
            google_oauth_refresh_token="rtoken",
        ):
            assert _check_server_key_available("Gmail Read") is True
            assert _check_server_key_available("Gmail Send") is True

    def test_calendar_with_oauth(self):
        with _patch_settings(
            google_oauth_client_id="cid",
            google_oauth_client_secret="csecret",
            google_oauth_refresh_token="rtoken",
        ):
            assert _check_server_key_available("Calendar List Events") is True

    def test_gmail_without_oauth(self):
        with _patch_settings():
            assert _check_server_key_available("Gmail Read") is False

    def test_unknown_tool(self):
        with _patch_settings():
            assert _check_server_key_available("Unknown Tool") is False

    def test_builtin_tool_returns_false(self):
        with _patch_settings():
            assert _check_server_key_available("Web Search") is False


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_tool(client: AsyncClient):
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
async def test_register_mcp_server(client: AsyncClient):
    resp = await client.post(
        "/api/tools/mcp-server",
        json={
            "name": "Google Workspace MCP",
            "url": "https://mcp.google-workspace.com",
            "auth_type": "api_key",
        },
    )
    assert resp.status_code == 201
    server = resp.json()
    assert server["name"] == "Google Workspace MCP"
    assert server["status"] == "active"


@pytest.mark.asyncio
async def test_delete_nonexistent_tool(client: AsyncClient):
    resp = await client.delete("/api/tools/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_custom_tool_has_server_key_available_false(client: AsyncClient):
    resp = await client.post(
        "/api/tools/custom",
        json={
            "name": "My API",
            "api_url": "https://example.com",
            "http_method": "GET",
        },
    )
    assert resp.status_code == 201
    tool = resp.json()
    assert tool["server_key_available"] is False


@pytest.mark.asyncio
async def test_prebuilt_tool_server_key_in_list(client: AsyncClient, db):
    from app.models.tool import Tool

    tool = Tool(
        type="prebuilt",
        is_system=True,
        name="Naver Blog Search",
        description="네이버 블로그 검색",
    )
    db.add(tool)
    await db.commit()

    with _patch_settings(naver_client_id="id", naver_client_secret="secret"):
        resp = await client.get("/api/tools")

    assert resp.status_code == 200
    tools = resp.json()
    naver_tool = next(t for t in tools if t["name"] == "Naver Blog Search")
    assert naver_tool["server_key_available"] is True
