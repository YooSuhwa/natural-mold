"""MCP decisions simulate prior flush visibility without touching persistent state."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.scoping import private_scope_predicate
from app.error_codes import marketplace_invalid_package
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.services.agent_blueprint_graph_types import (
    CredentialAssignment,
    GraphRequest,
    JsonObject,
    McpPlan,
    McpToolDraft,
    ServerDraft,
    creation_args,
    creation_mapping,
    credential_key,
    mapping,
    optional_text,
    sequence,
)


def matches(server: ServerDraft, spec: JsonObject) -> bool:
    return (
        server.transport == str(spec.get("transport") or "streamable_http")
        and server.url == optional_text(spec.get("url"))
        and server.command == optional_text(spec.get("command"))
        and server.args == tuple(sequence(spec.get("args")))
        and server.env_vars == mapping(spec.get("env_vars"))
        and server.headers == mapping(spec.get("headers"))
    )


async def plan_mcp(db: AsyncSession, request: GraphRequest) -> McpPlan:
    rows = sequence(request.capabilities.get("mcp_tools"))
    existing = (
        (
            await db.execute(
                select(
                    McpServer.id,
                    McpServer.name,
                    McpServer.description,
                    McpServer.transport,
                    McpServer.url,
                    McpServer.command,
                    McpServer.args,
                    McpServer.env_vars,
                    McpServer.headers,
                    McpServer.credential_id,
                ).where(private_scope_predicate(McpServer, request.context))
            )
        ).all()
        if rows
        else []
    )
    existing_ids = {row[0] for row in existing}
    pool = [
        ServerDraft(
            server_id,
            name,
            description,
            transport,
            url,
            command,
            tuple(args or []),
            env_vars or {},
            headers or {},
            credential_id,
        )
        for (
            server_id,
            name,
            description,
            transport,
            url,
            command,
            args,
            env_vars,
            headers,
            credential_id,
        ) in existing
    ]
    old_tools = (
        (
            await db.execute(
                select(McpTool.id, McpTool.server_id, McpTool.name).where(
                    McpTool.server_id.in_(existing_ids)
                )
            )
        ).all()
        if existing
        else []
    )
    tool_ids = {(server_id, name): tool_id for tool_id, server_id, name in old_tools}
    ids = []
    servers: list[ServerDraft] = []
    tools: list[McpToolDraft] = []
    assignments: list[CredentialAssignment] = []
    for raw in rows:
        row = mapping(raw)
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        spec = mapping(row.get("server"))
        server_name = str(spec.get("name") or "").strip()
        if not server_name:
            raise marketplace_invalid_package(f"missing MCP server dependency: {name}")
        credential_id = request.bindings.get(credential_key("mcp", server_name))
        server = None
        if request.body.dependency_strategy != "always_new":
            empty = None
            for candidate in pool:
                if candidate.name != server_name or not matches(candidate, spec):
                    continue
                if credential_id is None or candidate.credential_id == credential_id:
                    server = candidate
                    break
                if (
                    candidate.credential_id is None
                    and request.body.dependency_strategy != "reuse_existing"
                    and empty is None
                ):
                    empty = candidate
            if server is None:
                server = empty
        if server is None:
            if request.body.dependency_strategy == "reuse_existing":
                raise marketplace_invalid_package(f"missing MCP server dependency: {name}")
            server = ServerDraft(
                uuid4(),
                server_name,
                optional_text(spec.get("description")),
                str(spec.get("transport") or "streamable_http"),
                optional_text(spec.get("url")),
                optional_text(spec.get("command")),
                creation_args(spec.get("args")),
                creation_mapping(spec.get("env_vars")),
                creation_mapping(spec.get("headers")),
                credential_id,
            )
            servers.append(server)
            pool.append(server)
        elif credential_id is not None and server.credential_id is None:
            assigned = replace(server, credential_id=credential_id)
            pool[pool.index(server)] = assigned
            if server.id in existing_ids:
                assignments.append(CredentialAssignment(server.id, credential_id))
            else:
                servers[servers.index(server)] = assigned
            server = assigned
        tool_id = tool_ids.get((server.id, name))
        if tool_id is None:
            tool_id = uuid4()
            tools.append(
                McpToolDraft(
                    tool_id,
                    server.id,
                    name,
                    optional_text(row.get("description")),
                    row.get("input_schema") or {},
                )
            )
            tool_ids[(server.id, name)] = tool_id
        ids.append(tool_id)
    return McpPlan(tuple(ids), tuple(servers), tuple(tools), tuple(assignments))
