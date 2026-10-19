"""G-04 backend support: link ``required`` edits (S29), the connect item's credential type
(S23) and the panel's "소유자에게 알림" (S21, 14.2 ``connection.owner_needed``)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.models.tool import AgentToolLink
from app.services import readiness_cache
from tests.authz.d04_support import add_tool
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, add_skill, seed
from tests.authz.f05_support import CALLER, OWNER, per_user_tool, secret, shared_agent
from tests.authz.g01_support import AS_CALLER, enforce, link_tool, runnable
from tests.authz.h02_support import rows
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> AsyncIterator[None]:
    await seed(db)
    await add_member(db, THIRD_USER_ID)
    readiness_cache.invalidate_all()
    yield
    readiness_cache.invalidate_all()


@pytest.fixture
def as_service(test_app: FastAPI) -> Any:
    def install(service: AuthzService) -> None:
        async def current() -> AuthzService:
            return service

        test_app.dependency_overrides[get_authz] = current

    yield install
    test_app.dependency_overrides.pop(get_authz, None)


async def test_required_link_ids_set_and_clear_the_link_flag(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db, owner=CALLER)
    web = await add_tool(db, owner=CALLER, name="web")
    other = await add_tool(db, owner=CALLER, name="other")
    skill = await add_skill(db, owner=CALLER, slug="hr-guide")
    tools = [str(web.id), str(other.id)]
    body = {"tool_ids": tools, "skill_ids": [str(skill.id)], "required_link_ids": [str(web.id)]}
    response = await client.put(f"/api/agents/{agent.id}", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["required_link_ids"] == [str(web.id)]
    async with TestSession() as check:
        links = await check.scalars(select(AgentToolLink).where(AgentToolLink.agent_id == agent.id))
        assert {link.tool_id: link.required for link in links} == {web.id: True, other.id: False}
    response = await client.put(f"/api/agents/{agent.id}", json={"name": "renamed"})
    assert response.json()["required_link_ids"] == [str(web.id)]  # omitted: kept
    response = await client.put(
        f"/api/agents/{agent.id}", json={"required_link_ids": [str(skill.id)]}
    )
    assert response.json()["required_link_ids"] == [str(skill.id)]


async def test_connect_item_names_the_credential_type(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, as_service: Any
) -> None:
    agent = await shared_agent(db, mode="per_user")
    mine = await per_user_tool(db, agent)
    use = (AS_CALLER, "can_use", f"tool:{mine.id}")
    as_service(enforce(monkeypatch, *runnable(agent), use))
    body = (await client.get(f"/api/agents/{agent.id}/readiness")).json()
    connect = next(item for item in body["items"] if item["result"] == "connect_by_me")
    assert connect["credential"] == {
        "mode": "per_user",
        "status": "missing",
        "definition_key": "http_bearer",
    }


async def _owner_blocked_agent(db: AsyncSession) -> tuple[Any, Any]:
    agent = await shared_agent(db, mode="fixed")
    tool = await add_tool(db, owner=OWNER)
    credential = await secret(db, OWNER, "v")
    tool.credential_id = credential.id
    credential.status = "auth_needed"
    await db.commit()
    await link_tool(db, agent, tool, required=True)
    return agent, credential


async def test_notify_owner_sends_owner_needed_once_a_day(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, as_service: Any
) -> None:
    monkeypatch.setattr("app.authz.notification_events.async_session", TestSession)
    agent, credential = await _owner_blocked_agent(db)
    as_service(enforce(monkeypatch, *runnable(agent)))
    url = f"/api/agents/{agent.id}/readiness/notify-owner"
    response = await client.post(url)
    assert response.status_code == 202, response.text
    assert response.json() == {"notified": 1}
    assert (await client.post(url)).status_code == 202  # merged for 24 hours
    [row] = await rows(OWNER, "connection.owner_needed")
    assert (row.target_type, row.target_id) == ("credential", credential.id)


async def test_notify_owner_hides_agents_the_caller_cannot_run(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, _ = await _owner_blocked_agent(db)
    response = await client.post(f"/api/agents/{agent.id}/readiness/notify-owner")
    assert response.status_code == 404
    assert await rows(OWNER, "connection.owner_needed") == []


async def test_notify_owner_without_owner_items_sends_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db, owner=CALLER)
    response = await client.post(f"/api/agents/{agent.id}/readiness/notify-owner")
    assert response.status_code == 202, response.text
    assert response.json() == {"notified": 0}


async def test_notify_owner_reports_zero_when_nothing_could_be_written(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, as_service: Any
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("tenant session down")

    monkeypatch.setattr("app.authz.notification_events.tenant_session", broken)
    agent, _ = await _owner_blocked_agent(db)
    as_service(enforce(monkeypatch, *runnable(agent)))
    response = await client.post(f"/api/agents/{agent.id}/readiness/notify-owner")
    assert response.status_code == 202, response.text
    assert response.json() == {"notified": 0}
    assert await rows(OWNER, "connection.owner_needed") == []


async def test_owner_readiness_names_the_agent_only_for_config_viewers(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await add_agent(db, owner=CALLER)
    body = (await client.get(f"/api/agents/{agent.id}/readiness")).json()
    assert body["agent_id"] == str(agent.id)
