"""E-06: the share dialog sees an agent's delegated accounts before sharing (D17, 10.6).

``GET …/access`` carries ``delegations`` for agents (empty for other types) and sharing
without ``confirm_delegation`` is refused for every account the agent lends through its
links — not only through delegation grants, which F-02 writes later.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_subagent import AgentSubAgentLink
from app.models.tool import AgentToolLink
from tests.authz.d04_support import OTHER_USER_ID, AuthzSwitch, add_tool, allow, authz
from tests.authz.e01_support import add_agent, add_credential, base, seed

__all__ = ["authz"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _share(**extra: object) -> dict:
    return {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "consumer", **extra}


async def test_access_lists_the_accounts_an_agent_lends(
    client: AsyncClient, db: AsyncSession
) -> None:
    parent, child = await add_agent(db), await add_agent(db)
    tool = await add_tool(db, name="slack-post")
    credential = await add_credential(db)
    tool.credential_id = credential.id
    parent.llm_credential_id = credential.id
    db.add(AgentToolLink(agent_id=child.id, tool_id=tool.id))
    db.add(AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=child.id))
    await db.commit()
    access = await client.get(f"{base('agent', parent.id)}/access")
    assert access.status_code == 200, access.text
    found = {(d["resource_type"], d["resource_id"]) for d in access.json()["delegations"]}
    assert found == {("agent", str(parent.id)), ("tool", str(tool.id))}
    assert all(d["owner_name"] == "Test User" for d in access.json()["delegations"])
    plain = await client.get(f"{base('tool', tool.id)}/access")
    assert plain.json()["delegations"] == []


async def test_sharing_needs_confirmation_for_linked_accounts(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, tool = await add_agent(db), await add_tool(db)
    tool.credential_id = (await add_credential(db)).id
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    path = f"{base('agent', agent.id)}/grants"
    refused = await client.post(path, json=_share())
    assert refused.json()["error"]["code"] == "confirm_required"
    assert (await client.post(path, json=_share(confirm_delegation=True))).status_code == 201


async def test_an_agent_without_accounts_shares_directly(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=(await add_tool(db)).id))
    await db.commit()
    access = await client.get(f"{base('agent', agent.id)}/access")
    assert access.json()["delegations"] == []
    assert (
        await client.post(f"{base('agent', agent.id)}/grants", json=_share())
    ).status_code == 201


async def test_access_hides_delegations_from_callers_without_can_share(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    agent, tool = await add_agent(db, owner=OTHER_USER_ID), await add_tool(db, owner=OTHER_USER_ID)
    tool.credential_id = (await add_credential(db, owner=OTHER_USER_ID)).id
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    obj = f"agent:{agent.id}"
    authz.use(allow(("can_run", obj), ("can_revoke", obj), ("can_govern", obj)))
    governor = await client.get(f"{base('agent', agent.id)}/access")
    assert governor.status_code == 200, governor.text
    assert governor.json()["delegations"] == []
    authz.use(allow(("can_run", obj), ("can_revoke", obj), ("can_share", obj)))
    sharer = await client.get(f"{base('agent', agent.id)}/access")
    assert [d["resource_id"] for d in sharer.json()["delegations"]] == [str(tool.id)]


async def test_a_no_op_or_lower_share_does_not_ask_again(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, tool = await add_agent(db), await add_tool(db)
    tool.credential_id = (await add_credential(db)).id
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    path = f"{base('agent', agent.id)}/grants"
    editor = {**_share(confirm_delegation=True), "level": "editor"}
    assert (await client.post(path, json=editor)).status_code == 201
    same = await client.post(path, json={**_share(), "level": "editor"})
    assert same.status_code == 200, same.text  # existing grant, nothing written
    lower = await client.post(path, json=_share())
    assert lower.status_code == 201, lower.text  # consumer < editor: no new account exposure
    raised = await client.post(path, json={**_share(), "level": "manager"})
    assert raised.json()["error"]["code"] == "confirm_required"
