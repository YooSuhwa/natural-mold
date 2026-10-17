"""F-04: per-user connections ``/api/credential-bindings`` (plan 9.x, 11.3, 14.1)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource_credential_binding import ResourceCredentialBinding
from tests.authz.d04_support import AuthzSwitch, add_server, allow, authz
from tests.authz.e01_support import (
    OTHER_USER_ID,
    TEST_ORG_ID,
    add_credential,
    add_tool,
    audit_actions,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

__all__ = ["authz"]  # fixture re-export
URL = "/api/credential-bindings"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


KEYS = {"http_request": "http_bearer", "gmail_send": "google_workspace_oauth2"}


async def _per_user_tool(db: AsyncSession, *, owner=TEST_USER_ID, definition="http_request"):  # noqa: ANN001, ANN202
    tool = await add_tool(db, owner=owner)
    tool.definition_key, tool.credential_mode = definition, "per_user"
    tool.credential_definition_key = KEYS[definition]
    await db.commit()
    return tool


async def _rows() -> list[tuple]:
    async with TestSession() as db:
        rows = await db.execute(
            select(
                ResourceCredentialBinding.user_id,
                ResourceCredentialBinding.resource_id,
                ResourceCredentialBinding.credential_id,
            )
        )
        return [tuple(row) for row in rows]


async def test_connect_replace_list_and_disconnect(client: AsyncClient, db: AsyncSession) -> None:
    tool = await _per_user_tool(db)
    first, second = await add_credential(db), await add_credential(db)
    created = await client.put(f"{URL}/tool/{tool.id}", json={"credential_id": str(first.id)})
    assert created.status_code == 200, created.text
    replaced = await client.put(f"{URL}/tool/{tool.id}", json={"credential_id": str(second.id)})
    assert replaced.json()["id"] == created.json()["id"]
    listed = (await client.get(URL, params={"resource_type": "tool"})).json()
    assert [(b["resource_id"], b["credential_id"]) for b in listed] == [
        (str(tool.id), str(second.id))
    ]
    assert (await client.get(URL, params={"resource_type": "mcp_server"})).json() == []
    assert (await client.delete(f"{URL}/tool/{tool.id}")).status_code == 204
    assert (await client.get(URL)).json() == []
    again = await client.delete(f"{URL}/tool/{tool.id}")
    assert (again.status_code, again.json()["error"]["code"]) == (404, "not_found")
    assert await audit_actions(tool.id) == [
        "credential_binding.create",
        "credential_binding.create",
        "credential_binding.delete",
    ]


async def test_only_per_user_resources_take_connections(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)  # no credential: mode none
    credential = await add_credential(db)
    response = await client.put(f"{URL}/tool/{tool.id}", json={"credential_id": str(credential.id)})
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "credential_mode_not_per_user",
    )


async def test_the_credential_type_must_match_the_resource(
    client: AsyncClient, db: AsyncSession
) -> None:
    gmail = await _per_user_tool(db, definition="gmail_send")
    bearer = await add_credential(db, definition_key="http_bearer")
    response = await client.put(f"{URL}/tool/{gmail.id}", json={"credential_id": str(bearer.id)})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert (error["code"], error["details"]) == (
        "credential_type_mismatch",
        {"accepted": ["google_workspace_oauth2"]},
    )
    mail = await add_credential(db, definition_key="google_workspace_oauth2")
    ok = await client.put(f"{URL}/tool/{gmail.id}", json={"credential_id": str(mail.id)})
    assert ok.status_code == 200, ok.text


async def test_legacy_hides_foreign_tools_and_credentials(
    client: AsyncClient, db: AsyncSession
) -> None:
    foreign_tool = await _per_user_tool(db, owner=OTHER_USER_ID)
    mine = await _per_user_tool(db)
    foreign_credential = await add_credential(db, owner=OTHER_USER_ID)
    hidden = await client.put(
        f"{URL}/tool/{foreign_tool.id}", json={"credential_id": str(foreign_credential.id)}
    )
    assert (hidden.status_code, hidden.json()["error"]["code"]) == (404, "TOOL_NOT_FOUND")
    other = await client.put(
        f"{URL}/tool/{mine.id}", json={"credential_id": str(foreign_credential.id)}
    )
    assert other.status_code == 404  # the existing attach-time credential 404
    assert await _rows() == []


async def test_enforce_connects_my_credential_to_a_shared_resource(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    server, _ = await add_server(db, owner=OTHER_USER_ID)
    server.credential_mode, server.credential_definition_key = "per_user", "mcp_oauth2"
    await db.commit()
    token = await add_credential(db, definition_key="mcp_oauth2")
    authz.use(allow(("can_use", f"mcp_server:{server.id}")))
    denied = await client.put(
        f"{URL}/mcp_server/{server.id}", json={"credential_id": str(token.id)}
    )
    assert denied.status_code == 404, denied.text  # no credential#can_use
    authz.use(allow(("can_use", f"mcp_server:{server.id}"), ("can_use", f"credential:{token.id}")))
    ok = await client.put(f"{URL}/mcp_server/{server.id}", json={"credential_id": str(token.id)})
    assert ok.status_code == 200, ok.text
    assert await _rows() == [(TEST_USER_ID, server.id, token.id)]


async def test_another_users_connection_is_neither_listed_nor_removed(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await _per_user_tool(db)
    theirs = await add_credential(db, owner=OTHER_USER_ID)
    db.add(
        ResourceCredentialBinding(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=OTHER_USER_ID,
            resource_type="tool",
            resource_id=tool.id,
            credential_id=theirs.id,
        )
    )
    await db.commit()
    assert (await client.get(URL)).json() == []
    assert (await client.delete(f"{URL}/tool/{tool.id}")).status_code == 404
    assert await _rows() == [(OTHER_USER_ID, tool.id, theirs.id)]
    assert (await client.put(f"{URL}/agent/{tool.id}", json={})).status_code == 422


async def test_deleting_the_tool_or_the_credential_drops_connections(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool, other_tool = await _per_user_tool(db), await _per_user_tool(db)
    first, second = await add_credential(db), await add_credential(db)
    for target, credential in ((tool, first), (other_tool, second)):
        response = await client.put(
            f"{URL}/tool/{target.id}", json={"credential_id": str(credential.id)}
        )
        assert response.status_code == 200, response.text
    assert (await client.delete(f"/api/tools/{tool.id}")).status_code == 204
    assert await _rows() == [(TEST_USER_ID, other_tool.id, second.id)]
    assert (await client.delete(f"/api/credentials/{second.id}")).status_code == 204
    assert await _rows() == []


async def test_a_per_user_resource_without_a_recorded_type_takes_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, _ = await add_server(db)
    server.credential_mode = "per_user"
    await db.commit()
    token = await add_credential(db, definition_key="mcp_oauth2")
    response = await client.put(
        f"{URL}/mcp_server/{server.id}", json={"credential_id": str(token.id)}
    )
    assert (response.status_code, response.json()["error"]["details"]) == (
        422,
        {"accepted": []},
    )
