"""D-02: agent routes under legacy, shadow and enforce modes (plan 9.1, 8.4, 10.3)."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.authz.service import AuthzService, ShadowSample, Verdict
from app.models.agent import Agent
from app.schemas.agent import AgentResponse, AgentSummaryResponse
from tests.authz.agent_route_fixture import (
    ME,
    OTHER_USER_ID,
    AgentOwnerStore,
    DownAuthz,
    authz_override,
    enforce,
    seed_agent,
    shadow,
)
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.asyncio


async def test_legacy_bodies_keep_the_exact_pre_p3_fields(client: AsyncClient):
    agent_id = await seed_agent(owner=TEST_USER_ID)
    detail = (await client.get(f"/api/agents/{agent_id}")).json()
    listed = (await client.get("/api/agents")).json()
    summary = (await client.get("/api/agents/summary")).json()
    assert list(detail) == list(AgentResponse.model_fields)
    assert [list(item) for item in listed] == [list(AgentResponse.model_fields)]
    assert [list(item) for item in summary] == [list(AgentSummaryResponse.model_fields)]


async def test_legacy_hides_other_owners_agent_with_existing_404(client: AsyncClient):
    agent_id = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    response = await client.get(f"/api/agents/{agent_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert (await client.get("/api/agents")).json() == []


async def test_enforce_owner_gets_config_and_my_permissions(client: AsyncClient, test_app: FastAPI):
    agent_id = await seed_agent(owner=TEST_USER_ID)
    agent = uuid.UUID(agent_id)
    service = enforce(
        (ME, "can_run", agent), (ME, "can_view_config", agent), (ME, "can_edit", agent)
    )
    with authz_override(test_app, service):
        body = (await client.get(f"/api/agents/{agent_id}")).json()
    assert body["system_prompt"] == "secret prompt"
    assert body["my_permissions"] == ["can_run", "can_view_config", "can_edit"]


async def test_enforce_runner_only_gets_runner_view_without_settings(
    client: AsyncClient, test_app: FastAPI
):
    agent_id = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    with authz_override(test_app, enforce((ME, "can_run", uuid.UUID(agent_id)))):
        body = (await client.get(f"/api/agents/{agent_id}")).json()
    assert body["view"] == "runner"
    assert body["my_level"] == "consumer" and body["my_permissions"] == ["can_run"]
    assert body["owner_id"] == str(OTHER_USER_ID) and body["opener_questions"] == ["hi?"]
    assert not {"system_prompt", "tools", "mcp_tools", "middleware_configs"} & set(body)


async def test_enforce_without_can_run_is_the_existing_404(client: AsyncClient, test_app: FastAPI):
    agent_id = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    with authz_override(test_app, enforce()):
        detail = await client.get(f"/api/agents/{agent_id}")
        image = await client.get(f"/api/agents/{agent_id}/image")
    assert detail.status_code == 404 and detail.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert image.status_code == 404 and image.json()["error"]["code"] != "AGENT_NOT_FOUND"


async def test_enforce_runner_cannot_edit_and_gets_403(client: AsyncClient, test_app: FastAPI):
    agent_id = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    with authz_override(test_app, enforce((ME, "can_run", uuid.UUID(agent_id)))):
        put = await client.put(f"/api/agents/{agent_id}", json={"name": "x"})
        image = await client.post(f"/api/agents/{agent_id}/image")
    for response in (put, image):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_required"


async def test_enforce_outage_fails_closed_with_503(client: AsyncClient, test_app: FastAPI):
    agent_id = await seed_agent(owner=TEST_USER_ID)
    service = AuthzService(DownAuthz(), mode="enforce", enforce_types=frozenset({"agent"}))
    with authz_override(test_app, service):
        responses = [
            await client.get(f"/api/agents/{agent_id}"),
            await client.get("/api/agents"),
            await client.get("/api/agents/summary"),
        ]
    assert [r.status_code for r in responses] == [503, 503, 503]
    assert {r.json()["error"]["code"] for r in responses} == {"authz_unavailable"}


async def test_enforce_create_without_capability_is_403(client: AsyncClient, test_app: FastAPI):
    with authz_override(test_app, enforce(types=frozenset())):
        response = await client.post(
            "/api/agents",
            json={"name": "n", "system_prompt": "p", "model_id": str(uuid.uuid4())},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_required"


async def test_shadow_answers_legacy_and_records_samples(client: AsyncClient, test_app: FastAPI):
    agent_id = await seed_agent(owner=TEST_USER_ID)
    hidden = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    samples: list[ShadowSample] = []
    with authz_override(test_app, shadow(samples)):
        detail = await client.get(f"/api/agents/{agent_id}")
        missing = await client.get(f"/api/agents/{hidden}")
        listed = (await client.get("/api/agents")).json()
    assert detail.json()["my_permissions"][:2] == ["can_run", "can_view_config"]
    assert missing.status_code == 404
    assert [item["id"] for item in listed] == [agent_id]
    decided = {(s.permission, s.obj, s.legacy) for s in samples}
    assert ("can_run", f"agent:{agent_id}", Verdict.ALLOW) in decided
    assert ("can_run", f"agent:{hidden}", Verdict.NOT_FOUND) in decided


async def test_enforce_create_then_get_reads_its_own_write(
    client: AsyncClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    seeded = await seed_agent(owner=TEST_USER_ID)  # membership + a model to bind
    async with TestSession() as db:
        model_id = (await db.get(Agent, uuid.UUID(seeded))).model_id  # type: ignore[union-attr]
    store = AgentOwnerStore()
    with authz_override(test_app, AuthzService(store, mode="enforce")):
        created = await client.post(
            "/api/agents", json={"name": "fresh", "system_prompt": "p", "model_id": str(model_id)}
        )
        assert created.status_code == 201, created.text
        agent_id = created.json()["id"]
        detail = await client.get(f"/api/agents/{agent_id}")
    assert (ME, "owner", f"agent:{agent_id}") in {
        (t.user, t.relation, t.object) for t in store.stored()
    }
    assert created.json()["my_permissions"][0] == "can_run"
    assert detail.status_code == 200 and detail.json()["name"] == "fresh"
