"""Real GET route assertions; fixture insertion is not service-writer acceptance."""

from dataclasses import dataclass
from uuid import UUID

from httpx import AsyncClient
from pydantic import BaseModel, ConfigDict, TypeAdapter

from app.auth.cookies import ACTIVE_ORG_COOKIE
from tests.authz.native_private_roots import NativePrivateRows


class ResponseIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID


class ResponsePage(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: list[ResponseIdentity]


class ResponseActor(BaseModel):
    model_config = ConfigDict(frozen=True)
    is_super_user: bool


@dataclass(frozen=True, slots=True)
class DetailCase:
    table: str
    path: str
    row_id: UUID


@dataclass(frozen=True, slots=True)
class ListCase:
    table: str
    path: str
    page: bool = False


def detail_cases(rows: NativePrivateRows) -> tuple[DetailCase, ...]:
    ids = {row.__tablename__: row.id for row in rows.rows}
    conversation_path = f"/api/conversations/{ids['conversations']}"
    paths = {
        "agents": f"/api/agents/{ids['agents']}",
        "skills": f"/api/skills/{ids['skills']}",
        "mcp_servers": f"/api/mcp-servers/{ids['mcp_servers']}",
        "credentials": f"/api/credentials/{ids['credentials']}",
        "tools": f"/api/tools/{ids['tools']}",
        "marketplace_items": f"/api/marketplace/items/{ids['marketplace_items']}",
        "conversations": conversation_path,
        "builder_sessions": f"/api/builder/{ids['builder_sessions']}",
        "skill_builder_sessions": f"/api/skill-builder/{ids['skill_builder_sessions']}",
        "agent_blueprints": f"/api/agent-blueprints/{ids['agent_blueprints']}",
        "memory_proposals": f"/api/memory-proposals/{ids['memory_proposals']}",
        "share_links": f"{conversation_path}/share",
        "conversation_artifacts": (
            f"{conversation_path}/artifacts/{ids['conversation_artifacts']}"
        ),
    }
    return tuple(DetailCase(table, path, ids[table]) for table, path in paths.items())


def list_cases() -> tuple[ListCase, ...]:
    return (
        ListCase("agents", "/api/agents"),
        ListCase("skills", "/api/skills"),
        ListCase("tools", "/api/tools"),
        ListCase("mcp_servers", "/api/mcp-servers"),
        ListCase("credentials", "/api/credentials"),
        ListCase("marketplace_items", "/api/marketplace/items"),
        ListCase("conversations", "/api/conversations/page", True),
        ListCase("skill_builder_sessions", "/api/skill-builder"),
        ListCase("agent_blueprints", "/api/agent-blueprints"),
        ListCase("memory_records", "/api/memories"),
        ListCase("agent_triggers", "/api/triggers"),
        ListCase("agent_deployments", "/api/agent-api/deployments"),
        ListCase("agent_api_keys", "/api/agent-api/keys"),
        ListCase("conversation_artifacts", "/api/artifacts", True),
    )


async def assert_owner_http_boundaries(
    client: AsyncClient, first: NativePrivateRows, second: NativePrivateRows
) -> None:
    expected = {row.__tablename__: row.id for row in second.rows}
    client.cookies.delete(ACTIVE_ORG_COOKIE)
    client.cookies.set(ACTIVE_ORG_COOKIE, str(second.context.org_id))
    for case in detail_cases(second):
        response = await client.get(case.path)
        assert response.status_code == 200, (case.table, response.text)
        assert ResponseIdentity.model_validate_json(response.content).id == case.row_id, case.table
    for case in detail_cases(first):
        response = await client.get(case.path)
        assert response.status_code == 404, (case.table, response.text)
    for case in list_cases():
        response = await client.get(case.path)
        assert response.status_code == 200, (case.table, response.text)
        identities = (
            ResponsePage.model_validate_json(response.content).items
            if case.page
            else TypeAdapter(list[ResponseIdentity]).validate_json(response.content)
        )
        assert [identity.id for identity in identities] == [expected[case.table]], case.table


async def assert_operator_http_boundaries(client: AsyncClient, roots: NativePrivateRows) -> None:
    identity = await client.get("/api/auth/me")
    assert identity.status_code == 200, identity.text
    assert ResponseActor.model_validate_json(identity.content).is_super_user is True
    client.cookies.delete(ACTIVE_ORG_COOKIE)
    client.cookies.set(ACTIVE_ORG_COOKIE, str(roots.context.org_id))
    for case in detail_cases(roots):
        response = await client.get(case.path)
        assert response.status_code == 404, (case.table, response.text)
    for case in list_cases():
        response = await client.get(case.path)
        assert response.status_code == 200, (case.table, response.text)
        identities = (
            ResponsePage.model_validate_json(response.content).items
            if case.page
            else TypeAdapter(list[ResponseIdentity]).validate_json(response.content)
        )
        assert identities == [], case.table
