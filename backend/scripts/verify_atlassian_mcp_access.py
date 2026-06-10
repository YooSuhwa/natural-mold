from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.mcp import discovery
from app.mcp.auth import resolve_mcp_auth
from app.mcp.invocation import call_mcp_tool_once
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.user import User


def _text_payload(result: dict[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text") or ""))
    return "\n".join(texts)


def _extract_first_confluence_page_id(result: dict[str, Any]) -> str | None:
    text = _text_payload(result)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return None
    for row in results:
        if not isinstance(row, dict):
            continue
        content = row.get("content")
        if not isinstance(content, dict):
            continue
        if content.get("type") == "page" and content.get("id"):
            return str(content["id"])
    return None


def _find_tool(tools: list[McpTool], name: str) -> McpTool | None:
    for tool in tools:
        if tool.name == name:
            return tool
    return None


def _pick_tool(tools: list[McpTool]) -> McpTool:
    lowered = [(tool, tool.name.lower()) for tool in tools]
    priorities = [
        ("confluence", "search"),
        ("jira", "search"),
        ("page", "search"),
        ("issue", "search"),
        ("", "search"),
        ("confluence", "get"),
        ("jira", "get"),
        ("", "read"),
    ]
    for left, right in priorities:
        for tool, name in lowered:
            if left in name and right in name:
                return tool
    if tools:
        return tools[0]
    raise RuntimeError("No Atlassian MCP tools were discovered")


def _args_for_schema(schema: dict[str, Any], *, query: str, cloud_id: str | None) -> dict[str, Any]:
    props = schema.get("properties") if isinstance(schema, dict) else {}
    if not isinstance(props, dict):
        props = {}
    required = schema.get("required") if isinstance(schema, dict) else []
    if not isinstance(required, list):
        required = []

    out: dict[str, Any] = {}
    for key in props:
        normalized = key.lower()
        if normalized in {"query", "q", "text", "search", "searchterm", "search_term"}:
            out[key] = query
        elif normalized in {"cql", "cqlquery", "cql_query"}:
            out[key] = f'text ~ "{query}" AND type = page'
        elif normalized in {"cloudid", "cloud_id"} and cloud_id:
            out[key] = cloud_id
        elif normalized in {"limit", "maxresults", "max_results", "count"}:
            out[key] = 1

    for key in required:
        if key in out:
            continue
        normalized = str(key).lower()
        if normalized in {"query", "q", "text", "search", "searchterm", "search_term"}:
            out[str(key)] = query
        elif normalized in {"cql", "cqlquery", "cql_query"}:
            out[str(key)] = f'text ~ "{query}" AND type = page'
        elif normalized in {"cloudid", "cloud_id"} and cloud_id:
            out[str(key)] = cloud_id
        elif normalized in {"limit", "maxresults", "max_results", "count"}:
            out[str(key)] = 1
        else:
            raise RuntimeError(f"Cannot infer required argument '{key}' for tool schema")
    return out


async def _find_server(
    db: AsyncSession,
    *,
    server_name: str,
    user_email: str | None,
) -> McpServer:
    stmt = select(McpServer).where(McpServer.url.ilike("%mcp.atlassian.com%"))
    if server_name:
        stmt = stmt.where(McpServer.name.ilike(f"%{server_name}%"))
    if user_email:
        user = (await db.execute(select(User).where(User.email == user_email))).scalar_one_or_none()
        if user is None:
            raise RuntimeError(f"User '{user_email}' was not found")
        stmt = stmt.where(McpServer.user_id == user.id)
    stmt = stmt.order_by(McpServer.created_at.desc()).limit(1)
    server = (await db.execute(stmt)).scalar_one_or_none()
    if server is None:
        raise RuntimeError("No Atlassian MCP server was found")
    return server


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    async with async_session() as db:
        server = await _find_server(
            db,
            server_name=args.server_name,
            user_email=args.user_email,
        )
        probe, tools = await discovery.discover_tools(db, server)
        await db.commit()
        if not probe.get("success"):
            raise RuntimeError(f"MCP discovery failed: {probe.get('error')}")

        tool = _pick_tool(tools)
        arguments = _args_for_schema(
            tool.input_schema or {},
            query=args.query,
            cloud_id=args.cloud_id,
        )
        auth = await resolve_mcp_auth(
            db,
            credential_id=server.credential_id,
            user_id=server.user_id,
            static_headers=server.headers,
        )
        result = await call_mcp_tool_once(
            transport=server.transport,
            url=server.url,
            headers=auth.headers,
            credentials=auth.credentials,
            tool_name=tool.name,
            arguments=arguments,
        )
        await db.commit()
        result.update(
            {
                "server_id": str(server.id),
                "server_name": server.name,
                "tool_name": tool.name,
                "arguments": arguments,
            }
        )
        page_id = _extract_first_confluence_page_id(result)
        page_tool = _find_tool(tools, "getConfluencePage")
        if result.get("success") and page_id and page_tool and args.cloud_id:
            read_arguments = {
                "cloudId": args.cloud_id,
                "pageId": page_id,
                "contentFormat": "markdown",
            }
            read_result = await call_mcp_tool_once(
                transport=server.transport,
                url=server.url,
                headers=auth.headers,
                credentials=auth.credentials,
                tool_name=page_tool.name,
                arguments=read_arguments,
            )
            await db.commit()
            read_result.update(
                {
                    "tool_name": page_tool.name,
                    "arguments": read_arguments,
                }
            )
            result["read_result"] = read_result
        if not result.get("success"):
            return result
        if not result.get("content") and not result.get("structured_content"):
            result["success"] = False
            result["error"] = "MCP tool returned no content"
        read_result = result.get("read_result")
        if isinstance(read_result, dict) and not read_result.get("success"):
            result["success"] = False
            result["error"] = read_result.get("error") or "Confluence page read failed"
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Atlassian MCP content access")
    parser.add_argument("--server-name", default="Atlassian Rovo")
    parser.add_argument("--query", default=os.environ.get("E2E_ATLASSIAN_VERIFY_QUERY"))
    parser.add_argument("--cloud-id", default=os.environ.get("E2E_ATLASSIAN_CLOUD_ID"))
    parser.add_argument("--user-email", default=os.environ.get("E2E_USER_EMAIL"))
    args = parser.parse_args()
    if not args.query:
        raise SystemExit("--query or E2E_ATLASSIAN_VERIFY_QUERY is required")

    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
