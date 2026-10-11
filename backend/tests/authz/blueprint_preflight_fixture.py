"""SQL effect observation shared by Blueprint decision-boundary regressions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypedDict, Unpack

from pydantic import JsonValue
from sqlalchemy import event
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.engine.interfaces import DBAPICursor
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.agent_blueprint import AgentBlueprint


class SqlExecution(TypedDict):
    connection: Connection
    cursor: DBAPICursor
    statement: str
    parameters: tuple[JsonValue, ...]
    context: ExecutionContext
    executemany: bool


def blueprint(context: OrgContext, capabilities: dict[str, JsonValue]) -> AgentBlueprint:
    return AgentBlueprint(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Preflight graph",
        spec_hash="f" * 64,
        spec={
            "agent": {"name": "Preflight graph", "system_prompt": "Fixture"},
            "capabilities": capabilities,
            "setup": {"required_credentials": []},
        },
    )


def new_tool() -> list[JsonValue]:
    return [
        {
            "name": "New planned tool",
            "definition_key": "http_request",
            "parameters": {"url": "https://example.invalid"},
        }
    ]


@contextmanager
def materialization_sql(db: AsyncSession) -> Iterator[list[str]]:
    statements: list[str] = []
    tables = {
        "tools",
        "skills",
        "mcp_servers",
        "mcp_tools",
        "agents",
        "agent_tools",
        "agent_skills",
        "agent_mcp_tools",
        "agent_subagents",
        "agent_blueprints",
    }

    def observe(**execution: Unpack[SqlExecution]) -> None:
        statement = execution["statement"]
        words = statement.lower().split()
        if len(words) > 2 and words[0] in {"insert", "update", "delete"}:
            table = words[1] if words[0] == "update" else words[2]
            if table.strip('"') in tables:
                statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", observe, named=True)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", observe)
