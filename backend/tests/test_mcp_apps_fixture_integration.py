from __future__ import annotations

import asyncio
import socket
import sys
import time
from pathlib import Path

import pytest

from app.mcp.client import connect_and_list
from app.mcp.invocation import call_mcp_tool_once, read_mcp_resource_once


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        await asyncio.sleep(0.05)
    raise AssertionError("local MCP Apps fixture did not start")


@pytest.mark.asyncio
async def test_scripted_mcp_apps_server_discovery_resource_and_tool_call() -> None:
    port = _free_port()
    fixture = Path(__file__).parent / "fixtures" / "mcp_apps_server.py"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(fixture),
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        discovery = await connect_and_list(transport="streamable_http", url=url)
        resource = await read_mcp_resource_once(
            transport="streamable_http",
            url=url,
            headers=None,
            resource_uri="ui://weather/dashboard",
        )
        result = await call_mcp_tool_once(
            transport="streamable_http",
            url=url,
            headers=None,
            tool_name="weather",
            arguments={"city": "Seoul"},
        )
    finally:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

    assert discovery["success"] is True
    assert discovery["tools"][0]["metadata"] == {"ui": {"resourceUri": "ui://weather/dashboard"}}
    assert discovery["tools"][1]["metadata"] == {"ui": {"visibility": ["app"]}}
    assert resource["contents"][0]["uri"] == "ui://weather/dashboard"
    assert resource["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
    assert "Refresh weather" in resource["contents"][0]["text"]
    assert "rpc('tools/call'" in resource["contents"][0]["text"]
    assert resource["contents"][0]["_meta"] == {
        "ui": {
            "csp": {"connectDomains": ["https://api.weather.example"]},
            "prefersBorder": True,
        }
    }
    assert result["structured_content"] == {
        "city": "Seoul",
        "temperature": 23,
        "condition": "sunny",
    }
