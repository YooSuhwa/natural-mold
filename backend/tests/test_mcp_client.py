from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.agent_runtime.mcp_client import test_mcp_connection as mcp_test_connection


def _mock_response(status_code: int = 200, json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


class TestMcpConnection:
    async def test_success(self):
        init_resp = _mock_response(
            200, {"result": {"serverInfo": {"name": "test-server", "version": "1.0"}}}
        )
        tools_resp = _mock_response(
            200, {"result": {"tools": [{"name": "tool1"}, {"name": "tool2"}]}}
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, tools_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agent_runtime.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await mcp_test_connection("https://mcp.example.com")

        assert result["success"] is True
        assert result["server_info"] == {"name": "test-server", "version": "1.0"}
        assert len(result["tools"]) == 2

    async def test_connection_timeout(self):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agent_runtime.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await mcp_test_connection("https://mcp.example.com")

        assert result["success"] is False
        assert result["error"] == "Connection timeout"
        assert result["tools"] == []

    async def test_connection_error(self):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agent_runtime.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await mcp_test_connection("https://mcp.example.com")

        assert result["success"] is False
        assert result["error"] == "Cannot connect to server"
        assert result["tools"] == []

    async def test_non_200_status(self):
        init_resp = _mock_response(503, {})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=init_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agent_runtime.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await mcp_test_connection("https://mcp.example.com")

        assert result["success"] is False
        assert "503" in result["error"]
        assert result["tools"] == []

    async def test_invalid_json_response(self):
        mock_client = AsyncMock()
        bad_resp = MagicMock()
        bad_resp.status_code = 200
        bad_resp.json.side_effect = ValueError("Invalid JSON")
        mock_client.post = AsyncMock(return_value=bad_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agent_runtime.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await mcp_test_connection("https://mcp.example.com")

        assert result["success"] is False
        assert "Invalid JSON" in result["error"]

    async def test_with_auth_config_api_key(self):
        init_resp = _mock_response(200, {"result": {"serverInfo": {}}})
        tools_resp = _mock_response(200, {"result": {"tools": []}})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, tools_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agent_runtime.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await mcp_test_connection(
                "https://mcp.example.com",
                auth_config={"api_key": "secret-key"},
            )

        assert result["success"] is True
        # Verify auth header was included in the request
        call_kwargs = mock_client.post.call_args_list[0]
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Authorization") == "secret-key"

    async def test_with_auth_config_custom_header(self):
        init_resp = _mock_response(200, {"result": {"serverInfo": {}}})
        tools_resp = _mock_response(200, {"result": {"tools": []}})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, tools_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agent_runtime.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await mcp_test_connection(
                "https://mcp.example.com",
                auth_config={"api_key": "my-key", "header_name": "X-API-Key"},
            )

        assert result["success"] is True
        call_kwargs = mock_client.post.call_args_list[0]
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("X-API-Key") == "my-key"

    async def test_without_auth_config(self):
        init_resp = _mock_response(200, {"result": {"serverInfo": {}}})
        tools_resp = _mock_response(200, {"result": {"tools": []}})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[init_resp, tools_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.agent_runtime.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await mcp_test_connection("https://mcp.example.com")

        assert result["success"] is True
        # Only Content-Type header, no Authorization
        call_kwargs = mock_client.post.call_args_list[0]
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "Authorization" not in headers
