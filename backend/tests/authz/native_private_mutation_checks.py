"""Authenticated foreign mutation probes with physical row preservation checks."""

from collections.abc import Mapping
from dataclasses import dataclass

from httpx import AsyncClient
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from tests.authz.native_private_roots import NativePrivateRows


@dataclass(frozen=True, slots=True)
class MutationCase:
    method: str
    path: str
    body: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class MutationProbes:
    roots: NativePrivateRows
    csrf_token: str


@dataclass(frozen=True, slots=True)
class TableState:
    name: str
    values: tuple[str, ...]


async def capture_rows(db: AsyncSession, roots: NativePrivateRows) -> tuple[TableState, ...]:
    result: list[TableState] = []
    for root in roots.rows:
        table = Base.metadata.tables[root.__tablename__]
        row = (await db.execute(select(table).where(table.c.id == root.id))).one()
        result.append(TableState(root.__tablename__, tuple(repr(value) for value in row)))
    return tuple(result)


def mutation_cases(roots: NativePrivateRows) -> tuple[MutationCase, ...]:
    ids = {row.__tablename__: row.id for row in roots.rows}
    conversation = f"/api/conversations/{ids['conversations']}"
    return (
        MutationCase("PUT", f"/api/agents/{ids['agents']}", {"name": "Forbidden"}),
        MutationCase("DELETE", f"/api/agents/{ids['agents']}"),
        MutationCase("PATCH", f"/api/skills/{ids['skills']}", {"name": "Forbidden"}),
        MutationCase("DELETE", f"/api/skills/{ids['skills']}"),
        MutationCase("PATCH", f"/api/mcp-servers/{ids['mcp_servers']}", {"name": "Forbidden"}),
        MutationCase("DELETE", f"/api/mcp-servers/{ids['mcp_servers']}"),
        MutationCase("PATCH", f"/api/credentials/{ids['credentials']}", {"name": "Forbidden"}),
        MutationCase("DELETE", f"/api/credentials/{ids['credentials']}"),
        MutationCase("PATCH", f"/api/tools/{ids['tools']}", {"name": "Forbidden"}),
        MutationCase("DELETE", f"/api/tools/{ids['tools']}"),
        MutationCase("PATCH", conversation, {"title": "Forbidden"}),
        MutationCase("DELETE", conversation),
        MutationCase("POST", f"{conversation}/read"),
        MutationCase(
            "POST", f"{conversation}/messages/switch-branch", {"checkpoint_id": "fixture"}
        ),
        MutationCase("PUT", f"{conversation}/pinned-summary", {"message_id": "fixture"}),
        MutationCase("DELETE", f"{conversation}/pinned-summary"),
        MutationCase("POST", f"{conversation}/side-chats"),
        MutationCase("POST", f"{conversation}/side-chats/save"),
        MutationCase("DELETE", f"{conversation}/share"),
        MutationCase(
            "PATCH", f"/api/artifacts/{ids['conversation_artifacts']}", {"is_favorite": True}
        ),
        MutationCase("DELETE", f"{conversation}/artifacts/{ids['conversation_artifacts']}"),
        MutationCase("PATCH", f"/api/memories/{ids['memory_records']}", {"content": "Forbidden"}),
        MutationCase("DELETE", f"/api/memories/{ids['memory_records']}"),
        MutationCase("POST", f"/api/memory-proposals/{ids['memory_proposals']}/approve"),
        MutationCase("POST", f"/api/builder/{ids['builder_sessions']}/confirm"),
        MutationCase("POST", f"/api/skill-builder/{ids['skill_builder_sessions']}/confirm"),
        MutationCase("POST", f"/api/agent-blueprints/{ids['agent_blueprints']}/create-agent", {}),
        MutationCase("PATCH", f"/api/triggers/{ids['agent_triggers']}", {"name": "Forbidden"}),
        MutationCase("DELETE", f"/api/triggers/{ids['agent_triggers']}"),
        MutationCase(
            "PATCH",
            f"/api/agent-api/deployments/{ids['agent_deployments']}",
            {"status": "disabled"},
        ),
        MutationCase("POST", f"/api/agent-api/keys/{ids['agent_api_keys']}/revoke"),
        MutationCase(
            "PATCH", f"/api/marketplace/items/{ids['marketplace_items']}", {"name": "Forbidden"}
        ),
        MutationCase(
            "POST",
            f"/api/marketplace/installations/{ids['marketplace_installations']}/update",
            {"strategy": "keep_current"},
        ),
        MutationCase(
            "DELETE", f"/api/marketplace/installations/{ids['marketplace_installations']}"
        ),
    )


async def assert_foreign_mutations_denied(client: AsyncClient, probes: MutationProbes) -> None:
    for case in mutation_cases(probes.roots):
        response = await client.request(
            case.method,
            case.path,
            json=case.body,
            headers={"X-CSRF-Token": probes.csrf_token},
        )
        assert response.status_code == 404, (case.method, case.path, response.text)
