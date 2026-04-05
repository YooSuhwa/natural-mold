from __future__ import annotations

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings


async def test_mcp_connection(url: str, auth_config: dict | None = None) -> dict:
    """Test connection to an MCP server and discover tools."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth_config and auth_config.get("api_key"):
        header_name = auth_config.get("header_name", "Authorization")
        headers[header_name] = auth_config["api_key"]

    try:
        async with httpx.AsyncClient(timeout=settings.mcp_connection_timeout) as client:
            # Try MCP initialize handshake
            resp = await client.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "moldy", "version": "0.1.0"},
                    },
                    "id": 1,
                },
                headers=headers,
            )

            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Server returned status {resp.status_code}",
                    "tools": [],
                }

            data = resp.json()

            # Try to list tools
            tools_resp = await client.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/list",
                    "params": {},
                    "id": 2,
                },
                headers=headers,
            )

            tools = []
            if tools_resp.status_code == 200:
                tools_data = tools_resp.json()
                if "result" in tools_data and "tools" in tools_data["result"]:
                    tools = tools_data["result"]["tools"]

            return {
                "success": True,
                "server_info": data.get("result", {}).get("serverInfo", {}),
                "tools": tools,
            }

    except httpx.TimeoutException:
        return {"success": False, "error": "Connection timeout", "tools": []}
    except httpx.ConnectError:
        return {"success": False, "error": "Cannot connect to server", "tools": []}
    except Exception as e:
        return {"success": False, "error": str(e), "tools": []}


async def list_mcp_tools(url: str) -> list[dict]:
    """MCP 서버에서 도구 목록 발견."""
    try:
        async with (
            streamablehttp_client(url) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema,
                }
                for t in result.tools
            ]
    except Exception:
        return []
