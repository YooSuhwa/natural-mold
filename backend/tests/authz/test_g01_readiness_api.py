"""G-01: readiness routes, 12.3 visibility and the summary cache (plan 12.3)."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.models.tool import Tool
from app.models.user import User
from app.services import readiness_cache, readiness_service
from tests.authz.d04_support import add_tool
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, seed
from tests.authz.f05_support import CALLER, per_user_tool, shared_agent
from tests.authz.g01_support import AS_CALLER, enforce, link_tool, runnable
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


async def _shared_setup(db: AsyncSession) -> tuple[Any, Tool, Tool]:
    agent = await shared_agent(db, mode="per_user")
    denied = await link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID, name="secret"))
    mine = await per_user_tool(db, agent)
    return agent, denied, mine


async def test_get_my_agent_shows_every_item(client: AsyncClient, db: AsyncSession) -> None:
    agent = await add_agent(db, owner=CALLER)
    tool = await link_tool(db, agent, await add_tool(db, owner=CALLER, name="web"), required=True)
    response = await client.get(f"/api/agents/{agent.id}/readiness", params={"source": "CHAT"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overall"] == "ready" and body["identity_mode"] == agent.identity_mode
    assert {"kind": "tool", "id": str(tool.id), "agent_id": str(agent.id), "name": "web",
            "required": True, "result": "ready"} in body["items"]  # fmt: skip


async def test_get_hides_agents_the_caller_cannot_run(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await shared_agent(db, mode="fixed")
    assert (await client.get(f"/api/agents/{agent.id}/readiness")).status_code == 404
    assert (await client.get(f"/api/agents/{uuid4()}/readiness")).status_code == 404
    unknown = await client.get(f"/api/agents/{agent.id}/readiness", params={"source": "API"})
    assert unknown.status_code == 422


async def test_a_runner_sees_counts_for_what_it_cannot_see(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, as_service: Any
) -> None:
    agent, denied, mine = await _shared_setup(db)
    use = (AS_CALLER, "can_use", f"tool:{mine.id}")
    as_service(enforce(monkeypatch, *runnable(agent), use))
    response = await client.get(f"/api/agents/{agent.id}/readiness")
    assert response.status_code == 200, response.text
    body = response.json()
    assert str(denied.id) not in response.text and "secret" not in response.text
    assert str(agent.id) not in response.text
    assert body["overall"] == "limited"
    assert {
        "kind": "tool",
        "count": 1,
        "required": False,
        "result": "no_permission",
        "action": {"type": "request_access", "via": "agent_manager"},
    } in body["items"]
    assert {"kind": "llm_model", "count": 1, "required": True, "result": "ready"} in body["items"]
    connect = next(item for item in body["items"] if item["result"] == "connect_by_me")
    assert connect["id"] == str(mine.id) and connect["name"] == mine.name
    assert connect["credential"] == {
        "mode": "per_user",
        "status": "missing",
        "definition_key": "http_bearer",  # G-04: the type the caller connects
    }


async def test_a_config_viewer_sees_ids_and_names(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, as_service: Any
) -> None:
    agent, denied, _ = await _shared_setup(db)
    view = (AS_CALLER, "can_view_config", f"agent:{agent.id}")
    as_service(enforce(monkeypatch, *runnable(agent), view))
    body = (await client.get(f"/api/agents/{agent.id}/readiness")).json()
    item = next(item for item in body["items"] if item.get("id") == str(denied.id))
    assert (item["name"], item["result"]) == ("secret", "no_permission")


async def test_summary_omits_agents_the_caller_cannot_run(
    client: AsyncClient, db: AsyncSession
) -> None:
    mine = await add_agent(db, owner=CALLER)
    other = await shared_agent(db, mode="fixed")
    ids = [str(mine.id), str(other.id), str(uuid4())]
    response = await client.post("/api/agents/readiness-summary", json={"agent_ids": ids})
    assert response.status_code == 200, response.text
    assert response.json() == {str(mine.id): {"overall": "ready", "counts": {"ready": 1}}}
    too_many = {"agent_ids": [str(uuid4()) for _ in range(51)]}
    assert (await client.post("/api/agents/readiness-summary", json=too_many)).status_code == 422


async def test_summary_is_cached_until_a_watched_change_commits(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await add_agent(db, owner=CALLER)
    tool = await link_tool(db, agent, await add_tool(db, owner=CALLER))
    calls: list[object] = []
    real = readiness_service.assess_agent

    async def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[-1])
        return await real(*args, **kwargs)

    monkeypatch.setattr(readiness_service, "assess_agent", counted)

    async def overall() -> str:
        body = {"agent_ids": [str(agent.id)]}
        response = await client.post("/api/agents/readiness-summary", json=body)
        return response.json()[str(agent.id)]["overall"]

    assert await overall() == "ready"
    async with TestSession() as other:  # not a readiness input: the cache holds
        user = await other.get(User, CALLER)
        assert user is not None
        user.name = "renamed"
        await other.commit()
    assert await overall() == "ready" and len(calls) == 1
    async with TestSession() as other:  # bulk UPDATE of a watched table invalidates
        await other.execute(update(Tool).where(Tool.id == tool.id).values(enabled=False))
        await other.commit()
    assert await overall() == "limited" and len(calls) == 2
    async with TestSession() as other:  # ORM flush of a watched row invalidates
        row = await other.get(Tool, tool.id)
        assert row is not None
        row.enabled = True
        await other.commit()
    assert await overall() == "ready" and len(calls) == 3
