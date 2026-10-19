"""E-05 review: subagent and model-credential carriers, tool attach through shared agents,
check order and message, and the pre-E-05 grant report (plan 10.4)."""

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.cli import Arguments, parse, report_personal_shares
from app.authz.personal_account_carriers import agent_carriers
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.authz import ResourceGrant
from app.models.tool import AgentToolLink, Tool
from tests.authz.e01_support import (
    OTHER_USER_ID,
    TEST_ORG_ID,
    add_agent,
    add_credential,
    add_tool,
    base,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TestSession, engine


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _share() -> dict:
    return {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "consumer"}


def _grant(kind: str, rid: UUID, **extra: object) -> ResourceGrant:
    values: dict = {"relation": "viewer", "principal_type": "user", "principal_id": OTHER_USER_ID}
    values.update(extra)
    return ResourceGrant(
        org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, resource_type=kind, resource_id=rid, **values
    )


async def _chain(db: AsyncSession, depth: int) -> list[Agent]:
    agents = [await add_agent(db) for _ in range(depth)]
    for parent, sub in zip(agents, agents[1:], strict=False):
        db.add(AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=sub.id))
    db.add(AgentSubAgentLink(parent_agent_id=agents[-1].id, sub_agent_id=agents[0].id))  # cycle
    await db.commit()
    return agents


async def _personal_tool(db: AsyncSession, definition: str = "srt_account") -> Tool:
    tool = await add_tool(db, name=f"t-{definition}")
    tool.credential_id = (await add_credential(db, definition_key=definition)).id
    await db.commit()
    return tool


async def test_a_subagent_lending_a_personal_account_blocks_the_parent_share(
    client: AsyncClient, db: AsyncSession
) -> None:
    parent, _, grandchild = await _chain(db, 3)
    tool = await _personal_tool(db)
    db.add(AgentToolLink(agent_id=grandchild.id, tool_id=tool.id))
    await db.commit()
    response = await client.post(f"{base('agent', parent.id)}/grants", json=_share())
    assert response.status_code == 409, response.text
    [item] = response.json()["error"]["details"]["personal_accounts"]
    assert (item["resource_type"], item["resource_id"]) == ("tool", str(tool.id))


async def test_a_personal_model_credential_on_a_subagent_is_reported(
    client: AsyncClient, db: AsyncSession
) -> None:
    parent, child = await _chain(db, 2)
    child.llm_credential_id = (await add_credential(db, definition_key="ktx_account")).id
    await db.commit()
    response = await client.post(f"{base('agent', parent.id)}/grants", json=_share())
    [item] = response.json()["error"]["details"]["personal_accounts"]
    assert (item["resource_type"], item["resource_id"]) == ("agent", str(child.id))


@pytest.mark.parametrize("depth", [2, 5])
async def test_carriers_take_one_query_per_depth_level(db: AsyncSession, depth: int) -> None:
    agents = await _chain(db, depth)
    statements: list[int] = []

    def count(*_: object) -> None:
        statements.append(1)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        await agent_carriers(db, agents[0].id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    # one subagent read per depth level (the cycle back to the root ends it) + 4 carrier
    # reads + 2 fixed-agent per_user reads (F-04; connections read only when any are lent)
    assert len(statements) == depth + 6


async def test_personal_check_runs_before_the_delegation_confirmation(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db)
    tool = await _personal_tool(db)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    db.add(
        _grant(
            "tool",
            tool.id,
            relation="consumer",
            principal_type="agent",
            principal_id=agent.id,
            grant_kind="delegation",
        )
    )
    await db.commit()
    response = await client.post(f"{base('agent', agent.id)}/grants", json=_share())
    error = response.json()["error"]
    assert (response.status_code, error["code"]) == (409, "personal_account_shared")
    assert error["message"].startswith("개인 계정 자격 증명은 다른 사람과 공유할 수 없습니다")


async def test_a_tool_used_by_a_shared_agent_cannot_take_a_personal_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    direct, nested = await add_tool(db, name="direct"), await add_tool(db, name="nested")
    shared_agent, child = await _chain(db, 2)
    db.add(AgentToolLink(agent_id=shared_agent.id, tool_id=direct.id))
    db.add(AgentToolLink(agent_id=child.id, tool_id=nested.id))
    db.add(_grant("agent", shared_agent.id, relation="consumer"))
    gmail = await add_credential(db, definition_key="google_workspace_oauth2")
    for tool in (direct, nested):
        response = await client.patch(
            f"/api/tools/{tool.id}", json={"credential_id": str(gmail.id)}
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "personal_account_shared"


async def test_the_report_lists_pre_e05_shares_and_revokes_nothing(
    db: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert parse(["personal-account-shares", "--json"]).command == "personal-account-shares"
    credential = await add_credential(db, definition_key="foresttrip_account")
    plain = await add_credential(db, definition_key="http_bearer")
    tool = await _personal_tool(db)
    grant = _grant("credential", credential.id, relation="consumer")
    db.add_all(
        [grant, _grant("credential", plain.id, relation="consumer"), _grant("tool", tool.id)]
    )
    await db.commit()
    args = Arguments(command="personal-account-shares", as_json=True)
    assert await report_personal_shares(args, lambda: TestSession()) == 1
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {(r["resource_type"], r["resource_id"]) for r in rows} == {
        ("credential", str(credential.id)),
        ("tool", str(tool.id)),
    }
    await db.refresh(grant)
    assert grant.state == "active"
