"""E-05: personal account credentials are never lent to others (plan 10.4).

Until per-user credential modes exist (F-04/F-05) every credential on a tool, MCP
server or agent skill binding is the owner's (``shared`` mode), so a resource carrying
a personal account type cannot be shared, a personal credential cannot be shared or
transferred, and a shared tool cannot take one on.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import ResourceGrant
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink
from tests.authz.d04_support import add_server
from tests.authz.e01_support import (
    OTHER_USER_ID,
    TEST_ORG_ID,
    add_agent,
    add_credential,
    add_skill,
    add_tool,
    base,
    grants,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _share(level: str = "consumer") -> dict:
    return {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": level}


def _blocked(response, credential_id: object, definition: str) -> list[dict]:  # noqa: ANN001
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "personal_account_shared"
    found = error["details"]["personal_accounts"]
    assert {(f["credential_id"], f["definition_key"]) for f in found} == {
        (str(credential_id), definition)
    }
    return found


@pytest.mark.parametrize("definition", ["google_workspace_oauth2", "srt_account", "mcp_oauth2"])
async def test_a_personal_credential_is_not_shared(
    client: AsyncClient, db: AsyncSession, definition: str
) -> None:
    credential = await add_credential(db, definition_key=definition)
    response = await client.post(f"{base('credential', credential.id)}/grants", json=_share())
    _blocked(response, credential.id, definition)
    assert await grants(credential.id) == []


async def test_a_plain_credential_is_still_shared(client: AsyncClient, db: AsyncSession) -> None:
    credential = await add_credential(db, definition_key="http_bearer")
    response = await client.post(f"{base('credential', credential.id)}/grants", json=_share())
    assert response.status_code == 201, response.text


async def test_a_tool_carrying_a_personal_account_is_not_shared(
    client: AsyncClient, db: AsyncSession
) -> None:
    personal, plain = await add_tool(db, name="mail"), await add_tool(db, name="api")
    personal.credential_id = (await add_credential(db, definition_key="ktx_account")).id
    plain.credential_id = (await add_credential(db, definition_key="http_api_key")).id
    await db.commit()
    blocked = await client.post(f"{base('tool', personal.id)}/grants", json=_share("viewer"))
    [item] = _blocked(blocked, personal.credential_id, "ktx_account")
    assert (item["resource_type"], item["resource_id"]) == ("tool", str(personal.id))
    allowed = await client.post(f"{base('tool', plain.id)}/grants", json=_share("viewer"))
    assert allowed.status_code == 201, allowed.text


async def test_an_mcp_server_on_a_personal_oauth_account_is_not_shared(
    client: AsyncClient, db: AsyncSession
) -> None:
    server, _ = await add_server(db)
    server.credential_id = (await add_credential(db, definition_key="mcp_oauth2")).id
    await db.commit()
    response = await client.post(f"{base('mcp_server', server.id)}/grants", json=_share())
    _blocked(response, server.credential_id, "mcp_oauth2")


async def test_an_agent_lending_a_personal_account_is_not_shared(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool_agent, mcp_agent, skill_agent = [await add_agent(db) for _ in range(3)]
    tool = await add_tool(db)
    tool.credential_id = (await add_credential(db, definition_key="foresttrip_account")).id
    server, mcp_tool = await add_server(db)
    server.credential_id = (await add_credential(db, definition_key="google_workspace_oauth2")).id
    srt = await add_credential(db, definition_key="srt_account")
    skill = await add_skill(db, owner=TEST_USER_ID, slug="srt-booking")
    db.add(AgentToolLink(agent_id=tool_agent.id, tool_id=tool.id))
    db.add(AgentMcpToolLink(agent_id=mcp_agent.id, mcp_tool_id=mcp_tool.id))
    bindings = {"credential_bindings": {"srt": str(srt.id), "bad": "not-a-uuid"}}
    db.add(AgentSkillLink(agent_id=skill_agent.id, skill_id=skill.id, config=bindings))
    await db.commit()
    cases = [
        (tool_agent, tool.credential_id, "foresttrip_account", ("tool", tool.id)),
        (mcp_agent, server.credential_id, "google_workspace_oauth2", ("mcp_server", server.id)),
        (skill_agent, srt.id, "srt_account", ("skill", skill.id)),
    ]
    for agent, credential_id, definition, (via_type, via_id) in cases:
        response = await client.post(f"{base('agent', agent.id)}/grants", json=_share())
        [item] = _blocked(response, credential_id, definition)
        assert (item["resource_type"], item["resource_id"]) == (via_type, str(via_id))


async def test_an_agent_without_personal_accounts_is_shared(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db)
    response = await client.post(f"{base('agent', agent.id)}/grants", json=_share())
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("definition", ["ktx_account", "foresttrip_account", "mcp_oauth2"])
async def test_personal_credentials_are_never_transferred(
    client: AsyncClient, db: AsyncSession, definition: str
) -> None:
    credential = await add_credential(db, definition_key=definition)
    body = {"new_owner_id": str(OTHER_USER_ID)}
    response = await client.post(f"{base('credential', credential.id)}/transfer", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"personal_account": definition}


async def test_a_shared_tool_cannot_take_a_personal_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    shared, private = await add_tool(db, name="shared"), await add_tool(db, name="private")
    db.add(
        ResourceGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            resource_type="tool",
            resource_id=shared.id,
            relation="viewer",
            principal_type="user",
            principal_id=OTHER_USER_ID,
        )
    )
    gmail = await add_credential(db, definition_key="google_workspace_oauth2")
    token = await add_credential(db, definition_key="http_bearer")
    blocked = await client.patch(f"/api/tools/{shared.id}", json={"credential_id": str(gmail.id)})
    _blocked(blocked, gmail.id, "google_workspace_oauth2")
    await db.refresh(shared)
    assert shared.credential_id is None
    plain = await client.patch(f"/api/tools/{shared.id}", json={"credential_id": str(token.id)})
    assert plain.status_code == 200, plain.text
    mine = await client.patch(f"/api/tools/{private.id}", json={"credential_id": str(gmail.id)})
    assert mine.status_code == 200, mine.text
