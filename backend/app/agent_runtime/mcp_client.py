from __future__ import annotations

from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def extract_transport_headers(
    extra_config: dict[str, Any] | None,
) -> dict[str, str] | None:
    """connection.extra_config에서 MCP transport headers를 안전하게 추출.

    chat runtime / discovery probe / test endpoint 모두 같은 키를 사용하므로
    단일 진입점에서 dict 검증 + str 값 필터링을 수행한다.
    """
    if not extra_config:
        return None
    headers = extra_config.get("headers")
    if not isinstance(headers, dict):
        return None
    cleaned = {k: v for k, v in headers.items() if isinstance(v, str)}
    return cleaned or None


def _build_probe_headers(
    auth_config: dict[str, str] | None,
    extra_headers: dict[str, str] | None,
) -> dict[str, str] | None:
    """probe에 전달할 헤더 dict 합성. mcp transport(Content-Type/Accept)는 라이브러리가 처리.

    chat runtime이 사용하는 transport 헤더 + legacy `auth_config.api_key`(단일 헤더)를
    동일 위치에서 merge — 라이브러리에 None 또는 dict로 전달.
    """
    headers: dict[str, str] = {}
    if extra_headers:
        headers.update(extra_headers)
    if auth_config and auth_config.get("api_key"):
        header_name = auth_config.get("header_name", "Authorization")
        headers[header_name] = auth_config["api_key"]
    return headers or None


async def test_mcp_connection(
    url: str,
    auth_config: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """MCP 서버에 streamable-http 핸드셰이크로 연결 + 도구 목록 발견.

    raw HTTP는 streamable-http SSE 협상/Accept/세션 ID 등을 처리하지 못해 일부
    서버에서 406/SSE 파싱 실패. mcp library의 `streamablehttp_client`를 사용해
    chat runtime과 동일 transport로 probe한다 — 인증 헤더(extra_headers +
    auth_config)는 그대로 전달.
    """
    headers = _build_probe_headers(auth_config, extra_headers)

    try:
        async with (
            streamablehttp_client(url, headers=headers) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            init_result = await session.initialize()
            tools_result = await session.list_tools()

            server_info: dict[str, Any] = {}
            if init_result.serverInfo is not None:
                server_info = {
                    "name": init_result.serverInfo.name,
                    "version": init_result.serverInfo.version,
                }

            tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema,
                }
                for t in tools_result.tools
            ]
            return {"success": True, "server_info": server_info, "tools": tools}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e), "tools": []}


