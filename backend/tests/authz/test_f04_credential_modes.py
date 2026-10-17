"""F-04: credential modes (D15, S28) and the 10.4 "per_user may be shared" relaxation."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import AGENT_IDENTITY_PER_USER
from app.models.agent import Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink
from app.models.tool import AgentToolLink, Tool
from tests.authz.d04_support import AuthzSwitch, add_server, allow, authz
from tests.authz.e01_support import (
    OTHER_USER_ID,
    TEST_ORG_ID,
    add_agent,
    add_credential,
    add_tool,
    audit_actions,
    base,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

__all__ = ["authz"]  # fixture re-export
MAIL = "google_workspace_oauth2"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _share() -> dict:
    return {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "consumer"}


def _mode_url(tool: Tool) -> str:
    return f"/api/tools/{tool.id}/credential-mode"


async def _mode(model: type[Tool] | type[McpServer], row_id: object) -> str:
    async with TestSession() as db:
        row = await db.get(model, row_id)
        assert row is not None
        return row.credential_mode


async def _mail_tool(db: AsyncSession, mode: str = "shared") -> Tool:
    tool = await add_tool(db)
    tool.definition_key = "gmail_send"
    tool.credential_id = (await add_credential(db, definition_key=MAIL)).id
    tool.credential_mode = mode
    tool.credential_definition_key = MAIL if mode == "per_user" else None
    await db.commit()
    return tool


async def _caller_agent(db: AsyncSession) -> Agent:
    agent = await add_agent(db)
    agent.identity_mode = AGENT_IDENTITY_PER_USER
    await db.commit()
    return agent


async def test_mode_follows_the_credential_on_insert_and_update(
    client: AsyncClient, db: AsyncSession
) -> None:
    bare = await add_tool(db)
    credential = await add_credential(db)
    tool = Tool(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        definition_key="http_request",
        name="with",
        credential_id=credential.id,
    )
    db.add(tool)
    await db.commit()
    assert (bare.credential_mode, tool.credential_mode) == ("none", "shared")
    attached = await client.patch(
        f"/api/tools/{bare.id}", json={"credential_id": str(credential.id)}
    )
    assert attached.status_code == 200, attached.text
    assert await _mode(Tool, bare.id) == "shared"
    tool.credential_id = None
    await db.commit()
    assert await _mode(Tool, tool.id) == "none"


async def test_switching_to_per_user_records_the_required_type(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await _mail_tool(db)
    response = await client.put(_mode_url(tool), json={"credential_mode": "per_user"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "resource_type": "tool",
        "resource_id": str(tool.id),
        "credential_mode": "per_user",
        "credential_definition_key": MAIL,
        "binding_count": 0,
    }
    assert await audit_actions(tool.id) == ["credential_mode.update"]


@pytest.mark.parametrize("key", ["nope", "http_bearer"])
async def test_the_required_type_must_be_registered_and_accepted(
    client: AsyncClient, db: AsyncSession, key: str
) -> None:
    tool = await _mail_tool(db)
    body = {"credential_mode": "per_user", "credential_definition_key": key}
    response = await client.put(_mode_url(tool), json=body)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert (error["code"], error["details"]["accepted"]) == ("credential_mode_invalid", [MAIL])


async def test_an_mcp_server_needs_a_type_for_per_user(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, _ = await add_server(db)
    url = f"/api/mcp-servers/{server.id}/credential-mode"
    missing = await client.put(url, json={"credential_mode": "per_user"})
    assert missing.json()["error"]["code"] == "credential_mode_invalid"
    body = {"credential_mode": "per_user", "credential_definition_key": "mcp_oauth2"}
    assert (await client.put(url, json=body)).status_code == 200


async def test_modes_must_match_the_credential(client: AsyncClient, db: AsyncSession) -> None:
    bare, mail = await add_tool(db), await _mail_tool(db, "per_user")
    for tool, mode in ((bare, "shared"), (mail, "none")):
        response = await client.put(_mode_url(tool), json={"credential_mode": mode})
        assert (response.status_code, response.json()["error"]["code"]) == (
            422,
            "credential_mode_invalid",
        )
    assert await _mode(Tool, mail.id) == "per_user"


async def test_a_per_user_tool_is_shared_but_a_fixed_agent_still_lends_it(
    client: AsyncClient, db: AsyncSession
) -> None:
    shared, per_user = await _mail_tool(db), await _mail_tool(db, "per_user")
    blocked = await client.post(f"{base('tool', shared.id)}/grants", json=_share())
    assert blocked.status_code == 409
    assert "사용자별 연결" in blocked.json()["error"]["message"]
    allowed = await client.post(f"{base('tool', per_user.id)}/grants", json=_share())
    assert allowed.status_code == 201, allowed.text
    fixed, caller = await add_agent(db), await _caller_agent(db)
    for agent in (fixed, caller):
        db.add(AgentToolLink(agent_id=agent.id, tool_id=per_user.id))
    await db.commit()
    lent = await client.post(f"{base('agent', fixed.id)}/grants", json=_share())
    assert lent.json()["error"]["code"] == "personal_account_shared"
    [item] = lent.json()["error"]["details"]["personal_accounts"]
    assert (item["resource_id"], item["definition_key"]) == (str(per_user.id), MAIL)
    ok = await client.post(f"{base('agent', caller.id)}/grants", json=_share())
    assert ok.status_code == 201, ok.text


async def test_a_fixed_agent_lends_a_per_user_server_even_before_any_connection(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, mcp_tool = await add_server(db)
    server.credential_mode, server.credential_definition_key = "per_user", "mcp_oauth2"
    agent = await add_agent(db)
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))
    await db.commit()
    response = await client.post(f"{base('agent', agent.id)}/grants", json=_share())
    [item] = response.json()["error"]["details"]["personal_accounts"]
    assert (item["credential_id"], item["definition_key"]) == ("", "mcp_oauth2")


async def test_switching_back_to_shared_while_shared_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await _mail_tool(db, "per_user")
    assert (await client.post(f"{base('tool', tool.id)}/grants", json=_share())).status_code == 201
    response = await client.put(_mode_url(tool), json={"credential_mode": "shared"})
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "personal_account_shared",
    )
    assert await _mode(Tool, tool.id) == "per_user"
    other = await add_credential(db, definition_key=MAIL)
    patched = await client.patch(f"/api/tools/{tool.id}", json={"credential_id": str(other.id)})
    assert patched.status_code == 200, patched.text  # per_user takes it on while shared


async def test_an_mcp_server_used_by_a_shared_caller_agent_stays_per_user(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, mcp_tool = await add_server(db)
    server.credential_id = (await add_credential(db, definition_key="mcp_oauth2")).id
    server.credential_mode, server.credential_definition_key = "per_user", "mcp_oauth2"
    agent = await _caller_agent(db)
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))
    await db.commit()
    assert (
        await client.post(f"{base('agent', agent.id)}/grants", json=_share())
    ).status_code == 201
    response = await client.put(
        f"/api/mcp-servers/{server.id}/credential-mode", json={"credential_mode": "shared"}
    )
    assert response.json()["error"]["code"] == "personal_account_shared"
    assert await _mode(McpServer, server.id) == "per_user"


async def test_legacy_hides_a_foreign_tool(client: AsyncClient, db: AsyncSession) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    response = await client.put(_mode_url(tool), json={"credential_mode": "per_user"})
    assert (response.status_code, response.json()["error"]["code"]) == (404, "TOOL_NOT_FOUND")


async def test_enforce_leaving_per_user_needs_can_share(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db)
    tool.credential_id = (await add_credential(db)).id
    tool.credential_mode, tool.credential_definition_key = "per_user", "http_bearer"
    await db.commit()
    editor = (("can_use", f"tool:{tool.id}"), ("can_edit", f"tool:{tool.id}"))
    authz.use(allow(*editor))
    denied = await client.put(_mode_url(tool), json={"credential_mode": "shared"})
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_required")
    authz.use(
        allow(
            *editor,
            ("can_share", f"tool:{tool.id}"),
            ("can_use", f"credential:{tool.credential_id}"),
            ("can_share", f"credential:{tool.credential_id}"),
        )
    )
    ok = await client.put(_mode_url(tool), json={"credential_mode": "shared"})
    assert ok.status_code == 200, ok.text
    assert await _mode(Tool, tool.id) == "shared"


async def test_enforce_someone_elses_credential_is_a_clear_conflict(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    tool.credential_id = (await add_credential(db, owner=OTHER_USER_ID)).id
    tool.credential_mode, tool.credential_definition_key = "per_user", "http_bearer"
    await db.commit()
    resource = ("can_use", "can_edit", "can_share")
    authz.use(allow(*((p, f"tool:{tool.id}") for p in resource)))
    response = await client.put(_mode_url(tool), json={"credential_mode": "shared"})
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "credential_reshare_blocked",
    )
