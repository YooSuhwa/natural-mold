"""D-07: usage, builder and blueprint routes under legacy, shadow and enforce (9.1, 9.6)."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event

from app.auth.jwt import create_access_token
from app.authz.fake import FakeAuthz
from app.authz.policies import policy_kind
from app.authz.service import AuthzService, ShadowSample
from app.models.builder_session import BuilderSession
from app.routers import agent_blueprints, audit, auth, builder, templates, usage
from app.schemas.builder import BuilderStatus
from tests.authz.agent_route_fixture import (
    ME,
    ORG,
    OTHER_USER_ID,
    AgentOwnerStore,
    DownAuthz,
    authz_override,
    enforce,
    seed_agent,
    shadow,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession, engine

pytestmark = pytest.mark.asyncio
# The usage gate reads the session itself (not via overridable Depends), so send a token.
BEARER = {"Authorization": f"Bearer {create_access_token(TEST_USER_ID)}"}
DRAFT = {"name": "Bot", "name_ko": "봇", "system_prompt": "p", "tools": [], "middlewares": []}


def _no_cap() -> AuthzService:
    return AuthzService(FakeAuthz(), mode="enforce")


def _cap() -> AuthzService:
    return AuthzService(
        FakeAuthz(allowed=frozenset({(ME, "cap_create_agent", ORG)})), mode="enforce"
    )


async def _session(*, owner: uuid.UUID = TEST_USER_ID, draft: dict | None = DRAFT) -> str:
    async with TestSession() as db:
        row = BuilderSession(
            user_id=owner,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_request="r",
            status=BuilderStatus.PREVIEW,
            draft_config=draft,
        )
        db.add(row)
        await db.commit()
        return str(row.id)


async def test_every_d07_route_declares_a_non_legacy_policy():
    routers = (agent_blueprints, audit, auth, builder, templates, usage)
    kinds = {
        f"{sorted(route.methods)[0]} {route.path}": policy_kind(route.endpoint)
        for module in routers
        for route in module.router.routes
    }
    assert len(kinds) == 24
    assert {k: kind for k, kind in kinds.items() if kind in (None, "LEGACY")} == {}
    assert kinds["POST /api/builder"] == "CAP"
    assert kinds["GET /api/agents/{agent_id}/usage"] == "RES"
    assert {kinds[f"POST /api/auth/{p}"] for p in ("login", "register", "refresh")} == {"PUBLIC"}


async def test_legacy_agent_usage_still_answers_without_a_session(raw_client: AsyncClient):
    agent_id = await seed_agent(owner=OTHER_USER_ID)
    response = await raw_client.get(f"/api/agents/{agent_id}/usage")
    assert response.status_code == 200 and response.json()["total_tokens"] == 0


async def test_legacy_agent_usage_adds_no_sql_even_with_a_session(raw_client: AsyncClient):
    agent_id = await seed_agent(owner=OTHER_USER_ID)
    statements: list[str] = []

    def count(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        counts = []
        for headers in ({}, BEARER):
            statements.clear()
            response = await raw_client.get(f"/api/agents/{agent_id}/usage", headers=headers)
            assert response.status_code == 200
            counts.append(list(statements))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert len(counts[0]) == len(counts[1]) == 1  # only the pre-P3 token_usages aggregate
    assert "token_usages" in counts[1][0]


async def test_enforce_agent_usage_without_a_session_is_401(
    raw_client: AsyncClient, raw_test_app: FastAPI
):
    agent_id = await seed_agent(owner=OTHER_USER_ID)
    with authz_override(raw_test_app, enforce()):
        response = await raw_client.get(f"/api/agents/{agent_id}/usage")
    assert response.status_code == 401


async def test_enforce_agent_usage_needs_can_view_config(client: AsyncClient, test_app: FastAPI):
    agent_id = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    agent = uuid.UUID(agent_id)
    url = f"/api/agents/{agent_id}/usage"
    with authz_override(test_app, enforce((ME, "can_run", agent), (ME, "can_view_config", agent))):
        assert (await client.get(url, headers=BEARER)).status_code == 200
    with authz_override(test_app, enforce((ME, "can_run", agent))):
        runner = await client.get(url, headers=BEARER)
    with authz_override(test_app, enforce()):
        hidden = await client.get(url, headers=BEARER)
    with authz_override(test_app, AuthzService(DownAuthz(), mode="enforce")):
        down = await client.get(url, headers=BEARER)
    assert runner.status_code == 403 and runner.json()["error"]["code"] == "permission_required"
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert down.status_code == 503


async def test_shadow_agent_usage_answers_legacy_and_samples(
    client: AsyncClient, test_app: FastAPI
):
    agent_id = await seed_agent(owner=OTHER_USER_ID)
    samples: list[ShadowSample] = []
    with authz_override(test_app, shadow(samples)):
        response = await client.get(f"/api/agents/{agent_id}/usage", headers=BEARER)
    assert response.status_code == 200
    assert [(s.permission, s.obj) for s in samples] == [("can_view_config", f"agent:{agent_id}")]


async def test_enforce_builder_start_and_confirm_need_the_capability(
    client: AsyncClient, test_app: FastAPI
):
    session_id = await _session()
    with authz_override(test_app, _no_cap()):
        start = await client.post("/api/builder", json={"user_request": "x"})
        confirm = await client.post(f"/api/builder/{session_id}/confirm")
        blueprint = await client.post(f"/api/agent-blueprints/{uuid.uuid4()}/create-agent", json={})
    for response in (start, confirm, blueprint):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "capability_required"
    with authz_override(test_app, _cap()):
        assert (await client.post("/api/builder", json={"user_request": "x"})).status_code == 201


async def test_enforce_other_users_builder_session_stays_404(
    client: AsyncClient, test_app: FastAPI
):
    session_id = await _session(owner=OTHER_USER_ID)
    with authz_override(test_app, _cap()):
        responses = [
            await client.get(f"/api/builder/{session_id}"),
            await client.post(f"/api/builder/{session_id}/confirm"),
        ]
    assert {r.json()["error"]["code"] for r in responses} == {"SESSION_NOT_FOUND"}


async def test_shadow_builder_start_answers_legacy_and_samples_the_capability(
    client: AsyncClient, test_app: FastAPI
):
    samples: list[ShadowSample] = []
    with authz_override(test_app, shadow(samples)):
        response = await client.post("/api/builder", json={"user_request": "x"})
    assert response.status_code == 201
    assert [(s.permission, s.obj) for s in samples] == [("cap_create_agent", ORG)]


async def test_enforce_confirm_writes_owner_tuples_before_responding(
    client: AsyncClient, test_app: FastAPI, monkeypatch
):
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    from tests.test_builder_router import _seed

    async with TestSession() as db:
        await _seed(db)
    session_id = await _session()
    store = AgentOwnerStore()
    with authz_override(test_app, AuthzService(store, mode="enforce")):
        response = await client.post(f"/api/builder/{session_id}/confirm")
    assert response.status_code == 201
    agent = f"agent:{response.json()['id']}"
    assert (ME, "owner", agent) in {(t.user, t.relation, t.object) for t in store.stored()}
