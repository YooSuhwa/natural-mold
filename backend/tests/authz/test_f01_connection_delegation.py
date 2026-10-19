"""F-01 (plan m86, 10.6): link delegation columns and the delegation backfill.

Done-when: connection count = delegation grant count — one active ``delegation`` grant
per delegated (agent, object) pair, where an MCP server counts once per agent however
many of its tools are linked (10.6).
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Connection,
    MetaData,
    Table,
    create_engine,
    insert,
    inspect,
    select,
    text,
)

import app.models  # noqa: F401
from alembic import op as alembic_op
from app.database import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
LINKS = ("agent_tools", "agent_mcp_tools", "agent_skills", "agent_subagents")
NEW = {"required", "delegated_by", "link_status", "broken_reason"}
T = Base.metadata.tables


def _load() -> ModuleType:
    path = VERSIONS / "f01_connection_delegation.py"
    spec = importlib.util.spec_from_file_location("f01_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class Lane:
    connection: Connection
    migration: ModuleType
    tenant: UUID
    org: UUID
    owner: UUID
    model: UUID

    def add(self, table: str, **values: object) -> UUID:
        target = T[table]
        if table in LINKS:  # reflected: the ORM link tables carry the m86 defaults
            target = Table(table, MetaData(), autoload_with=self.connection)
            values = {k: v.hex if isinstance(v, UUID) else v for k, v in values.items()}
        if "id" in target.c:
            values.setdefault("id", uuid4())
        self.connection.execute(insert(target).values(**values))
        return values.get("id")  # type: ignore[return-value]

    def agent(self, **values: object) -> UUID:
        agent_id = uuid4()
        return self.add(
            "agents",
            id=agent_id,
            user_id=self.owner,
            org_id=self.org,
            tenant_id=self.tenant,
            name="A",
            system_prompt="",
            runtime_name=f"agent_{agent_id.hex[:8]}",
            model_id=self.model,
            **values,
        )

    def scoped(self) -> dict[str, object]:
        return {"user_id": self.owner, "org_id": self.org, "tenant_id": self.tenant}


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch) -> Iterator[Lane]:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(alembic_op, "_proxy", Operations(context), raising=False)
        migration = _load()
        migration.downgrade()  # back to f04: the link tables without the m86 columns
        tenant, org, owner = uuid4(), uuid4(), uuid4()
        connection.execute(
            insert(T["tenants"]).values(
                id=tenant, name="T", slug="t", secrets_namespace=f"tenants/{tenant}"
            )
        )
        connection.execute(
            insert(T["organizations"]).values(id=org, tenant_id=tenant, name="O", slug="o")
        )
        connection.execute(insert(T["users"]).values(id=owner, email="o@x.test", name="O"))
        model = uuid4()
        connection.execute(
            insert(T["models"]).values(id=model, provider="p", model_name="m", display_name="M")
        )
        yield Lane(connection, migration, tenant, org, owner, model)
    engine.dispose()


def _columns(connection: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table)}


def _delegations(connection: Connection) -> list[tuple[object, ...]]:
    grants = T["resource_grants"]
    return [
        tuple(row)
        for row in connection.execute(
            select(
                grants.c.resource_type,
                grants.c.resource_id,
                grants.c.principal_id,
                grants.c.granted_by,
                grants.c.condition_name,
                grants.c.condition_context,
                grants.c.source_link,
                grants.c.org_id,
            ).where(grants.c.grant_kind == "delegation", grants.c.state == "active")
        ).all()
    ]


def _seed_graph(lane: Lane) -> tuple[UUID, UUID, dict[str, UUID]]:
    parent, child = lane.agent(), lane.agent()
    credential = lane.add(
        "credentials",
        definition_key="openai",
        name="C",
        data_encrypted="x",
        key_id="k",
        **lane.scoped(),
    )
    lane.connection.execute(
        text("UPDATE agents SET llm_credential_id = :c WHERE id = :a"),
        {"c": credential.hex, "a": parent.hex},
    )
    tool = lane.add("tools", definition_key="http_request", name="t", **lane.scoped())
    skill = lane.add("skills", name="s", slug="s", **lane.scoped())
    server = lane.add("mcp_servers", name="m", transport="sse", **lane.scoped())
    first, second = (lane.add("mcp_tools", server_id=server, name=n) for n in ("a", "b"))
    lane.add("agent_tools", agent_id=parent, tool_id=tool)
    lane.add("agent_tools", agent_id=child, tool_id=tool)
    lane.add("agent_mcp_tools", agent_id=parent, mcp_tool_id=first)
    lane.add("agent_mcp_tools", agent_id=parent, mcp_tool_id=second)
    lane.add("agent_skills", agent_id=parent, skill_id=skill)
    lane.add(
        "agent_subagents",
        parent_agent_id=parent,
        sub_agent_id=child,
        position=0,
        created_at=datetime(2026, 1, 1),
    )
    ids = {"tool": tool, "skill": skill, "mcp_server": server, "credential": credential}
    return parent, child, ids


def test_revision_chains_on_the_f04_head() -> None:
    migration = _load()
    assert migration.revision == "f01_connection_delegation"
    assert migration.down_revision == "f04_credential_modes"


def test_upgrade_adds_columns_and_one_grant_per_delegated_connection(lane: Lane) -> None:
    assert not NEW & _columns(lane.connection, "agent_tools")
    parent, child, ids = _seed_graph(lane)

    lane.migration.upgrade()

    for table in LINKS:
        assert _columns(lane.connection, table) >= NEW
    assert "llm_credential_delegated_by" in _columns(lane.connection, "agents")
    links = lane.connection.execute(
        text(
            "SELECT 'tool', agent_id, tool_id FROM agent_tools UNION "
            "SELECT 'mcp_server', l.agent_id, t.server_id FROM agent_mcp_tools l "
            "JOIN mcp_tools t ON t.id = l.mcp_tool_id UNION "
            "SELECT 'skill', agent_id, skill_id FROM agent_skills UNION "
            "SELECT 'agent', parent_agent_id, sub_agent_id FROM agent_subagents UNION "
            "SELECT 'credential', id, llm_credential_id FROM agents "
            "WHERE llm_credential_id IS NOT NULL"
        )
    ).all()
    grants = _delegations(lane.connection)
    assert len(links) == len(grants) == 6  # connection count = delegation grant count
    by_key = {(g[0], g[1], g[2]): g for g in grants}
    expected = {
        ("tool", ids["tool"], parent),
        ("tool", ids["tool"], child),
        ("mcp_server", ids["mcp_server"], parent),
        ("skill", ids["skill"], parent),
        ("agent", child, parent),
        ("credential", ids["credential"], parent),
    }
    assert set(by_key) == expected
    assert {g[3] for g in grants} == {lane.owner} and {g[7] for g in grants} == {lane.org}
    server = by_key[("mcp_server", ids["mcp_server"], parent)]
    assert server[4] == "allowed_run_source"
    assert server[5] == {"allowed_sources": ["CHAT", "TRIGGER", "CHANNEL", "SUBAGENT", "API"]}
    assert server[6] == {"link": "agent_mcp_tools", "migration": "m86"}
    assert by_key[("tool", ids["tool"], parent)][4] is None
    for table in LINKS:
        rows = lane.connection.execute(
            text(f"SELECT delegated_by, required, link_status, broken_reason FROM {table}")  # noqa: S608
        ).all()
        assert {(UUID(str(r[0])), bool(r[1]), r[2], r[3]) for r in rows} == {
            (lane.owner, False, "active", None)
        }
    owner = lane.connection.scalar(
        text("SELECT llm_credential_delegated_by FROM agents WHERE id = :a"), {"a": parent.hex}
    )
    assert UUID(str(owner)) == lane.owner


async def test_orm_link_defaults_and_delegator_set_null() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        await connection.run_sync(Base.metadata.create_all)
        lane = Lane(connection, ModuleType("orm"), uuid4(), uuid4(), uuid4(), uuid4())  # type: ignore[arg-type]
        await connection.run_sync(_orm_defaults, lane)
    await engine.dispose()


def _orm_defaults(connection: Connection, lane: Lane) -> None:
    lane.connection = connection
    connection.execute(
        insert(T["tenants"]).values(
            id=lane.tenant, name="T", slug="t", secrets_namespace=f"tenants/{lane.tenant}"
        )
    )
    lane.add("organizations", id=lane.org, tenant_id=lane.tenant, name="O", slug="o")
    lane.add("users", id=lane.owner, email="o@x.test", name="O")
    lane.add("models", id=lane.model, provider="p", model_name="m", display_name="M")
    agent = lane.agent()
    editor = lane.add("users", email="e@x.test", name="E")
    tool = lane.add("tools", definition_key="http_request", name="t", **lane.scoped())
    lane.add("agent_tools", agent_id=agent, tool_id=tool, delegated_by=editor)
    row = connection.execute(text("SELECT required, link_status FROM agent_tools")).one()
    assert (bool(row[0]), row[1]) == (False, "active")
    connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": editor.hex})
    assert connection.scalar(text("SELECT delegated_by FROM agent_tools")) is None
    with pytest.raises(Exception, match="CHECK"):
        connection.execute(text("UPDATE agent_tools SET link_status = 'gone'"))
