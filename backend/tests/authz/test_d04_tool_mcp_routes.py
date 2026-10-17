"""D-04 tool and MCP routes: legacy parity, shadow samples, enforce 404/403/503 (8.4, 9.5)."""

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import policy_kind
from app.authz.service_types import Verdict
from app.main import create_app
from tests.authz.d04_support import (
    OTHER_ORG_ID,
    OTHER_USER_ID,
    AuthzSwitch,
    Unavailable,
    add_server,
    add_tool,
    allow,
    authz,
    grant_to_me,
    seed_scope,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID

__all__ = ["authz"]  # fixture re-export
D04_MODULES = {
    "app.routers.tools",
    "app.routers.mcp",
    "app.routers.credentials",
    "app.routers.credential_oauth",
    "app.routers.models",
    "app.routers.health",
    "app.routers.system_llm_settings",
}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


def test_every_d04_route_declares_a_reviewed_non_legacy_policy() -> None:
    routes = [
        route
        for route in create_app().routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ in D04_MODULES
    ]
    kinds = {policy_kind(route.endpoint) for route in routes}
    assert len(routes) == 54
    assert kinds == {"CATALOG", "CANDIDATES", "CAP", "RES", "SELF", "SUPER", "PUBLIC"}


async def test_legacy_mode_keeps_the_existing_not_found_body(client: AsyncClient, db) -> None:
    other = await add_tool(db, owner=OTHER_USER_ID)
    response = await client.get(f"/api/tools/{other.id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TOOL_NOT_FOUND"


async def test_shadow_answers_legacy_and_samples_hidden_rows(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    mine, other = await add_tool(db), await add_tool(db, owner=OTHER_USER_ID)
    authz.use(mode="shadow")
    assert (await client.get(f"/api/tools/{mine.id}")).status_code == 200
    assert (await client.get(f"/api/tools/{other.id}")).status_code == 404
    samples = [(s.obj, s.permission, s.legacy, s.resource_org_id) for s in authz.samples]
    assert samples == [
        (f"tool:{mine.id}", "can_view", Verdict.ALLOW, TEST_ORG_ID),
        (f"tool:{other.id}", "can_view", Verdict.NOT_FOUND, TEST_ORG_ID),
    ]


async def test_enforce_tool_detail_follows_the_8_4_table(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    mine, shared = await add_tool(db), await add_tool(db, owner=OTHER_USER_ID)
    sibling = await add_tool(db, owner=OTHER_USER_ID, org=OTHER_ORG_ID)
    authz.use(
        allow(
            ("can_use", f"tool:{mine.id}"),
            ("can_use", f"tool:{shared.id}"),
            ("can_view", f"tool:{shared.id}"),
            ("can_use", f"tool:{sibling.id}"),
            ("can_view", f"tool:{sibling.id}"),
        )
    )
    denied = await client.get(f"/api/tools/{mine.id}")
    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"required": "can_view", "request_access": True}
    assert (await client.get(f"/api/tools/{shared.id}")).status_code == 200
    foreign = await client.get(f"/api/tools/{sibling.id}")
    assert (foreign.status_code, foreign.json()["error"]["code"]) == (404, "TOOL_NOT_FOUND")


async def test_enforce_hides_owned_rows_without_a_tuple_and_fails_closed(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    mine = await add_tool(db)
    authz.use()
    hidden = await client.get(f"/api/tools/{mine.id}")
    assert (hidden.status_code, hidden.json()["error"]["code"]) == (404, "TOOL_NOT_FOUND")
    authz.use(client=Unavailable())
    assert (await client.get(f"/api/tools/{mine.id}")).status_code == 503


async def test_enforce_tool_list_confirms_grant_candidates(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    mine, shared = await add_tool(db, name="mine"), await add_tool(db, owner=OTHER_USER_ID)
    unshared = await add_tool(db, owner=OTHER_USER_ID, name="unshared")
    await grant_to_me(db, "tool", shared.id)
    authz.use(
        allow(
            ("can_use", f"tool:{shared.id}"),
            ("can_use", f"tool:{unshared.id}"),  # no grant row: never a candidate
        )
    )
    ids = {row["id"] for row in (await client.get("/api/tools")).json()}
    assert ids == {str(shared.id)}
    assert str(mine.id) not in ids


async def test_enforce_create_requires_the_capability(
    client: AsyncClient, authz: AuthzSwitch
) -> None:
    body = {"definition_key": "http_request", "name": "n", "parameters": {}}
    authz.use()
    denied = await client.post("/api/tools", json=body)
    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"capability": "cap_create_tool"}


async def test_enforce_mcp_detail_and_all_tools_check_server_and_tool(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    server, tool = await add_server(db, owner=OTHER_USER_ID)
    blocked_server, blocked = await add_server(db, owner=OTHER_USER_ID, name="blocked")
    await grant_to_me(db, "mcp_server", server.id)
    await grant_to_me(db, "mcp_server", blocked_server.id)
    authz.use(
        allow(
            ("can_use", f"mcp_server:{server.id}"),
            ("can_view", f"mcp_server:{server.id}"),
            ("can_invoke", f"mcp_tool:{tool.id}"),
            ("can_use", f"mcp_server:{blocked_server.id}"),
        )
    )
    detail = await client.get(f"/api/mcp-servers/{server.id}")
    assert detail.status_code == 200
    assert [t["id"] for t in detail.json()["tools"]] == [str(tool.id)]
    edit = await client.patch(f"/api/mcp-servers/{server.id}", json={"description": "x"})
    assert edit.json()["error"]["details"]["required"] == "can_edit"
    listed = await client.get("/api/mcp-servers/all-tools")
    assert [row["id"] for row in listed.json()] == [str(tool.id)]
    assert str(blocked.id) not in {row["id"] for row in listed.json()}
