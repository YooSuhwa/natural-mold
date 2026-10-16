"""D-04 review: MCP secret masking, export keys, system servers, org checks, zero queries."""

from collections.abc import Iterator
from contextlib import suppress
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.runtime import legacy_service
from app.credentials.creation import CredentialCreation
from app.dependencies import CurrentUser, get_current_user
from app.exceptions import AppError
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.user import User
from app.services import tool_service
from tests.authz.d04_support import (
    OTHER_ORG_ID,
    OTHER_USER_ID,
    AuthzSwitch,
    add_server,
    add_tool,
    allow,
    authz,
    grant_to_me,
    seed_scope,
)
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.conftest import TEST_USER_ID, engine

__all__ = ["authz"]  # fixture re-export
SECRETS = {"headers": {"Authorization": "Bearer s"}, "env_vars": {"TOKEN": "v"}}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _secret_server(db: AsyncSession, owner=OTHER_USER_ID) -> McpServer:
    server, _ = await add_server(db, owner=owner)
    server.headers, server.env_vars = SECRETS["headers"], SECRETS["env_vars"]
    await db.commit()
    return server


async def test_grantee_without_edit_sees_keys_but_not_values(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    server = await _secret_server(db)
    await grant_to_me(db, "mcp_server", server.id)
    view = (("can_use", f"mcp_server:{server.id}"), ("can_view", f"mcp_server:{server.id}"))
    authz.use(allow(*view))
    masked = {"Authorization": "<redacted>"}
    assert (await client.get("/api/mcp-servers")).json()[0]["headers"] == masked
    detail = (await client.get(f"/api/mcp-servers/{server.id}")).json()
    assert (detail["headers"], detail["env_vars"]) == (masked, {"TOKEN": "<redacted>"})
    exported = (await client.get("/api/mcp-servers/export")).json()["mcpServers"]
    assert list(exported) == ["srv (Other)"]
    assert exported["srv (Other)"]["headers"] == masked
    authz.use(allow(*view, ("can_edit", f"mcp_server:{server.id}")))
    assert (await client.get(f"/api/mcp-servers/{server.id}")).json()["headers"] == (
        SECRETS["headers"]
    )


async def test_owner_values_and_export_keys_are_unchanged_in_legacy(client: AsyncClient, db):
    await _secret_server(db, owner=TEST_USER_ID)
    assert (await client.get("/api/mcp-servers")).json()[0]["headers"] == SECRETS["headers"]
    exported = (await client.get("/api/mcp-servers/export")).json()["mcpServers"]
    assert list(exported) == ["srv"]
    assert exported["srv"]["env"] == SECRETS["env_vars"]


async def test_export_keeps_owned_and_granted_same_name_rows_apart(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    mine, _ = await add_server(db)
    theirs, _ = await add_server(db, owner=OTHER_USER_ID)
    await grant_to_me(db, "mcp_server", theirs.id)
    authz.use(
        allow(*((p, f"mcp_server:{s.id}") for s in (mine, theirs) for p in ("can_use", "can_view")))
    )
    exported = (await client.get("/api/mcp-servers/export")).json()["mcpServers"]
    assert sorted(exported) == ["srv", "srv (Other)"]


async def test_system_mcp_server_is_decided_by_catalog_fanout_when_enforced(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    # E-03 resolved the D-04 deferral: OpenFGA (the org fanout) decides system servers.
    system = McpServer(
        user_id=OTHER_USER_ID, is_system=True, name="sys", transport="stdio", command="x"
    )
    db.add(system)
    await db.commit()
    obj = f"mcp_server:{system.id}"
    query = {"target_kind": "mcp_server", "target_id": str(system.id)}
    authz.use(allow())  # catalog item off: no fanout tuple, hidden like a missing row
    detail = await client.get(f"/api/mcp-servers/{system.id}")
    assert (detail.status_code, detail.json()["error"]["code"]) == (404, "MCP_SERVER_NOT_FOUND")
    assert (await client.get("/api/health/history", params=query)).status_code == 404
    authz.use(allow(("can_use", obj), ("can_view", obj)))
    assert (await client.get(f"/api/mcp-servers/{system.id}")).status_code == 200
    assert (await client.get("/api/health/history", params=query)).status_code == 200
    listed = (await client.get("/api/mcp-servers")).json()
    assert str(system.id) in {row["id"] for row in listed}


@pytest.fixture
def no_org_user(test_app: FastAPI, db: AsyncSession) -> Iterator[None]:
    stranger = CurrentUser(
        id=uuid5(NAMESPACE_URL, "moldy/d04-no-org"),
        email="s@test.com",
        name="S",
        is_super_user=False,
    )
    db.add(User(id=stranger.id, email=stranger.email, name=stranger.name))

    async def override() -> CurrentUser:
        return stranger

    previous = test_app.dependency_overrides[get_current_user]
    test_app.dependency_overrides[get_current_user] = override
    yield
    test_app.dependency_overrides[get_current_user] = previous


async def test_shadow_models_tolerate_a_caller_without_organization(
    client: AsyncClient, db, authz: AuthzSwitch, no_org_user: None
) -> None:
    await db.commit()
    authz.use(mode="shadow")
    assert (await client.get("/api/models")).status_code == 200
    assert authz.samples == []
    authz.use()  # llm_model enforced: the organization is required
    assert (await client.get("/api/models")).status_code != 200


async def _credential(db: AsyncSession):
    creation = CredentialCreation(definition_key="http_bearer", name="c", data={"token": "x"})
    credential = await create_test_credential(db, creation, unit_context(TEST_USER_ID))
    await db.commit()
    return credential


async def test_health_check_rejects_a_credential_from_another_org(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    server, _ = await add_server(db)
    credential = await _credential(db)
    credential.org_id = OTHER_ORG_ID
    await db.commit()
    s, c = f"mcp_server:{server.id}", f"credential:{credential.id}"
    authz.use(allow(("can_use", s), ("can_edit", s), ("can_use", c)))
    query = {"target_kind": "mcp_server", "target_id": str(server.id)}
    response = await client.post(
        "/api/health/check", params={**query, "credential_id": str(credential.id)}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "CREDENTIAL_NOT_FOUND",
    )


async def test_health_model_check_gates_the_explicit_credential(
    client: AsyncClient, db, authz: AuthzSwitch
) -> None:
    model = Model(provider="openai", model_name="m", display_name="m", is_visible=True)
    db.add(model)
    credential = await _credential(db)
    authz.use(allow(("can_use", f"llm_model:{model.id}")))
    query = {"target_kind": "model", "target_id": str(model.id)}
    response = await client.post(
        "/api/health/check", params={**query, "credential_id": str(credential.id)}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "CREDENTIAL_NOT_FOUND",
    )


async def test_gate_row_adds_no_sql_in_legacy_mode(db: AsyncSession) -> None:
    mine, other = await add_tool(db), await add_tool(db, owner=OTHER_USER_ID)
    context, statements = unit_context(TEST_USER_ID), []

    def count(*_: object) -> None:
        statements.append(1)

    async def run(tool_id, **kwargs) -> int:
        statements.clear()
        with suppress(AppError):
            await tool_service.load_owned(db, tool_id, context, **kwargs)
        return len(statements)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        for tool in (mine, other):
            baseline = await run(tool.id)
            assert baseline > 0
            assert await run(tool.id, authz=legacy_service()) == baseline
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
