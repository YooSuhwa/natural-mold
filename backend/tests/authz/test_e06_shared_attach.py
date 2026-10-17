"""E-06: attaching accounts to an agent that is already shared re-runs D17 and 10.4.

Closes the E-05 gap "attachments after the agent is shared": an agent that is shared
(directly or as a subagent of a shared agent) and gains an account carrier needs
``confirm_delegation`` (``409 confirm_required`` lists what is now lent), and a personal
account is refused outright. Unshared agents and links the agent already had are
unchanged (legacy behaviour).
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.write_tools import build_write_tools
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.audit_event import AuditEvent
from app.models.authz import ResourceGrant
from app.models.tool import AgentToolLink, Tool
from tests.authz.d04_support import OTHER_USER_ID, add_tool
from tests.authz.e01_support import add_agent, add_credential, seed
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _share(db: AsyncSession, agent: Agent) -> None:
    db.add(
        ResourceGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            resource_type="agent",
            resource_id=agent.id,
            relation="consumer",
            principal_type="user",
            principal_id=OTHER_USER_ID,
        )
    )
    await db.commit()


async def _account_tool(db: AsyncSession, definition: str = "http_bearer") -> Tool:
    tool = await add_tool(db, name=f"t-{uuid.uuid4().hex[:6]}")
    tool.credential_id = (await add_credential(db, definition_key=definition)).id
    await db.commit()
    return tool


async def _linked(agent_id: uuid.UUID) -> set[uuid.UUID]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(AgentToolLink.tool_id).where(AgentToolLink.agent_id == agent_id)
        )
        return set(rows)


def _put(client: AsyncClient, agent: Agent, tools: list[Tool], **extra: object):  # noqa: ANN202
    body = {"tool_ids": [str(t.id) for t in tools], **extra}
    return client.put(f"/api/agents/{agent.id}", json=body)


async def test_a_shared_agent_confirms_a_new_account_tool(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, tool = await add_agent(db), await _account_tool(db)
    await _share(db, agent)
    first = await _put(client, agent, [tool])
    assert first.status_code == 409, first.text
    error = first.json()["error"]
    assert error["code"] == "confirm_required"
    assert error["details"]["delegations"] == [
        {
            "resource_type": "tool",
            "resource_id": str(tool.id),
            "name": tool.name,
            "credential_id": str(tool.credential_id),
            "credential_name": "Slack",
            "owner_name": "Test User",
        }
    ]
    assert await _linked(agent.id) == set()  # nothing was written
    confirmed = await _put(client, agent, [tool], confirm_delegation=True)
    assert confirmed.status_code == 200, confirmed.text
    assert await _linked(agent.id) == {tool.id}
    [event] = await _events(agent.id, "agent.delegation_confirm")
    assert event.event_metadata == {
        "delegations": [
            {
                "resource_type": "tool",
                "resource_id": str(tool.id),
                "credential_id": str(tool.credential_id),
            }
        ]
    }
    again = await _put(client, agent, [tool])  # the carrier is not new any more
    assert again.status_code == 200, again.text
    assert len(await _events(agent.id, "agent.delegation_confirm")) == 1


async def _events(agent_id: uuid.UUID, action: str) -> list[AuditEvent]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(AuditEvent).where(
                AuditEvent.target_id == str(agent_id), AuditEvent.action == action
            )
        )
        return list(rows)


async def test_an_unshared_agent_attaches_without_confirmation(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, tool = await add_agent(db), await _account_tool(db)
    response = await _put(client, agent, [tool])
    assert response.status_code == 200, response.text


async def test_a_shared_agent_refuses_a_personal_account_even_confirmed(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, tool = await add_agent(db), await _account_tool(db, "google_workspace_oauth2")
    await _share(db, agent)
    response = await _put(client, agent, [tool], confirm_delegation=True)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "personal_account_shared"
    assert await _linked(agent.id) == set()


async def test_a_subagent_of_a_shared_agent_confirms_too(
    client: AsyncClient, db: AsyncSession
) -> None:
    parent, child, tool = await add_agent(db), await add_agent(db), await _account_tool(db)
    db.add(AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=child.id))
    await db.commit()
    await _share(db, parent)
    response = await _put(client, child, [tool])
    assert response.json()["error"]["code"] == "confirm_required"


async def test_attaching_a_subagent_that_lends_an_account_confirms(
    client: AsyncClient, db: AsyncSession
) -> None:
    parent, child, tool = await add_agent(db), await add_agent(db), await _account_tool(db)
    db.add(AgentToolLink(agent_id=child.id, tool_id=tool.id))
    await db.commit()
    await _share(db, parent)
    path = f"/api/agents/{parent.id}"
    refused = await client.put(path, json={"sub_agent_ids": [str(child.id)]})
    [item] = refused.json()["error"]["details"]["delegations"]
    assert (item["resource_type"], item["resource_id"]) == ("tool", str(tool.id))
    body = {"sub_agent_ids": [str(child.id)], "confirm_delegation": True}
    assert (await client.put(path, json=body)).status_code == 200


async def test_the_assistant_never_confirms_for_a_shared_agent(db: AsyncSession) -> None:
    shared, private = await add_agent(db), await add_agent(db)
    tool = await _account_tool(db)
    await _share(db, shared)

    def add_tool_tool(agent: Agent):  # noqa: ANN202
        tools = build_write_tools(None, agent.id, unit_context(TEST_USER_ID))  # type: ignore[arg-type]
        return next(t for t in tools if t.name == "add_tool_to_agent")

    factory = "app.agent_runtime.assistant.tools.write_tools.async_session_factory"
    with patch(factory, TestSession):
        refused = await add_tool_tool(shared).ainvoke({"tool_names": [tool.name]})
        added = await add_tool_tool(private).ainvoke({"tool_names": [tool.name]})
    assert "에이전트 설정 화면에서" in refused and tool.name in refused
    assert await _linked(shared.id) == set()
    assert added.startswith("도구 추가 완료")
    assert await _linked(private.id) == {tool.id}
