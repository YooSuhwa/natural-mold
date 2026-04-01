from __future__ import annotations

from typing import Any

import httpx
from langchain_core.tools import StructuredTool

from app.config import settings


def _build_http_tool_func(
    api_url: str,
    http_method: str,
    auth_type: str | None,
    auth_config: dict[str, Any] | None,
) -> Any:
    async def call_api(**kwargs: Any) -> str:
        headers: dict[str, str] = {}
        if auth_type == "api_key" and auth_config:
            header_name = auth_config.get("header_name", "Authorization")
            key = auth_config.get("api_key", "")
            headers[header_name] = key
        elif auth_type == "bearer" and auth_config:
            token = auth_config.get("token", "")
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
            method = http_method.upper()
            if method == "GET":
                resp = await client.get(api_url, params=kwargs, headers=headers)
            elif method == "POST":
                resp = await client.post(api_url, json=kwargs, headers=headers)
            elif method == "PUT":
                resp = await client.put(api_url, json=kwargs, headers=headers)
            elif method == "DELETE":
                resp = await client.delete(api_url, params=kwargs, headers=headers)
            else:
                resp = await client.get(api_url, params=kwargs, headers=headers)

            return resp.text

    return call_api


def create_tool_from_db(
    name: str,
    description: str | None,
    api_url: str,
    http_method: str,
    parameters_schema: dict[str, Any] | None,
    auth_type: str | None,
    auth_config: dict[str, Any] | None,
) -> StructuredTool:
    func = _build_http_tool_func(api_url, http_method, auth_type, auth_config)

    return StructuredTool.from_function(
        coroutine=func,
        name=name,
        description=description or f"Call {name}",
        args_schema=None,  # LangChain will infer from function signature
    )
