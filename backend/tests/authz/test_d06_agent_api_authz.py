"""D-06: Agent API deployment/key routes and the KEY-policy runtime API (plan 9.2)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.authz.policies import CANDIDATES, KEY, PRIVATE, PUBLIC, policy_of
from app.authz.runtime import get_authz
from app.authz.service import AuthzService, ShadowSample, Verdict
from app.main import create_app
from tests.authz.authz_service_testkit import RecordingClient
from tests.conftest import TEST_USER_ID
from tests.test_agent_api_control_plane import _seed_agent
from tests.test_agent_runtime_public_api import _seed_deployed_agent_with_key

pytestmark = pytest.mark.anyio
USER = f"user:{TEST_USER_ID}"
DOMAIN = {"app.routers.agent_api", "app.routers.agent_runtime_api", "app.routers.triggers"}


def test_d06_routes_declare_plan_policies() -> None:
    routes = {
        f"{method} {route.path}": policy_of(route.endpoint)
        for route in create_app().routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ in DOMAIN
        for method in route.methods
    }
    assert len(routes) == 28
    v1 = {k: p for k, p in routes.items() if " /v1/" in k}
    assert v1.pop("GET /v1/health") == PUBLIC
    assert len(v1) == 10 and set(v1.values()) == {KEY("can_deploy")}
    assert routes["GET /api/agent-api/deployment-candidates"] == CANDIDATES("agent", "can_deploy")
    assert routes["POST /api/agent-api/keys/{api_key_id}/revoke"] == PRIVATE(
        "agent_api_keys", param="api_key_id"
    )
    rest = [p for k, p in routes.items() if "/v1/" not in k and "candidates" not in k]
    assert len(rest) == 16 and {p.kind for p in rest if p is not None} == {"PRIVATE"}


@pytest.fixture
def install(test_app: FastAPI) -> Iterator:
    """Override only the route dependency: deep service checks must use the bound one."""

    def _install(service: AuthzService) -> AuthzService:
        test_app.dependency_overrides[get_authz] = lambda: service
        return service

    yield _install
    test_app.dependency_overrides.pop(get_authz, None)


def _service(
    mode: str,
    allowed: Iterable[tuple[str, str, str]] = (),
    *,
    unavailable: bool = False,
    samples: list[ShadowSample] | None = None,
) -> AuthzService:
    hook = samples.append if samples is not None else None
    client = RecordingClient(allowed=frozenset(allowed), unavailable=unavailable)
    return AuthzService(client, mode=mode, shadow_hook=hook)  # type: ignore[arg-type]


def _code(response) -> str:
    return response.json()["error"]["code"]


def _deployer(agent_id) -> set[tuple[str, str, str]]:
    return {(USER, "can_run", f"agent:{agent_id}"), (USER, "can_deploy", f"agent:{agent_id}")}


async def test_runtime_refuses_key_whose_owner_lost_can_deploy(client, db, install) -> None:
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    auth = {"Authorization": f"Bearer {cleartext}"}
    body = {"agent_id": deployment.public_id}
    install(_service("enforce"))
    hidden = await client.post("/v1/threads", headers=auth, json=body)
    assert hidden.status_code == 404 and _code(hidden) == "AGENT_DEPLOYMENT_NOT_FOUND"
    listed = await client.get("/v1/agents", headers=auth)
    assert listed.status_code == 200 and listed.json() == {"data": []}  # the key stays valid
    install(_service("enforce", {(USER, "can_run", f"agent:{agent.id}")}))
    runner = await client.post("/v1/threads", headers=auth, json=body)  # opaque on /v1
    assert runner.status_code == 404 and _code(runner) == "AGENT_DEPLOYMENT_NOT_FOUND"
    install(_service("enforce", _deployer(agent.id)))
    created = await client.post("/v1/threads", headers=auth, json=body)
    assert created.status_code == 201
    listed = await client.get("/v1/agents", headers=auth)
    assert [row["id"] for row in listed.json()["data"]] == [deployment.public_id]


async def test_runtime_keeps_opaque_401_and_fails_closed(client, db, install) -> None:
    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    install(_service("enforce", unavailable=True))
    bad = await client.post(
        "/v1/threads",
        headers={"Authorization": "Bearer moldy_sk_bogus"},
        json={"agent_id": deployment.public_id},
    )
    assert bad.status_code == 401 and _code(bad) == "AGENT_API_KEY_INVALID"
    down = await client.post(
        "/v1/threads",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={"agent_id": deployment.public_id},
    )
    assert down.status_code == 503 and _code(down) == "authz_unavailable"
    assert (await client.get("/v1/health")).status_code == 200


async def test_thread_lookup_and_run_wait_recheck_per_request(
    client, db, install, monkeypatch
) -> None:
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    auth = {"Authorization": f"Bearer {cleartext}"}
    thread = await client.post("/v1/threads", headers=auth, json={"agent_id": deployment.public_id})
    thread_id = thread.json()["id"]

    async def fake_invoke(cfg, messages_history, **kwargs):
        return "ok"

    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_invoke", fake_invoke)
    install(_service("enforce"))
    lookup = await client.get(f"/v1/threads/{thread_id}", headers=auth)
    assert lookup.status_code == 404 and _code(lookup) == "AGENT_API_THREAD_NOT_FOUND"
    run = {
        "agent_id": deployment.public_id,
        "input": {"messages": [{"role": "user", "content": "x"}]},
    }
    denied = await client.post("/v1/runs/wait", headers=auth, json=run)
    assert denied.status_code == 404 and _code(denied) == "AGENT_DEPLOYMENT_NOT_FOUND"
    install(_service("enforce", _deployer(agent.id)))
    assert (await client.get(f"/v1/threads/{thread_id}", headers=auth)).status_code == 200
    allowed = await client.post("/v1/runs/wait", headers=auth, json=run)
    assert allowed.status_code == 200 and allowed.json()["output"] == {"answer": "ok"}


async def test_shadow_answers_legacy_and_samples_can_deploy(client, db, install) -> None:
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    samples: list[ShadowSample] = []
    install(_service("shadow", samples=samples))  # OpenFGA would deny everything
    response = await client.post(
        "/v1/threads",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={"agent_id": deployment.public_id},
    )
    assert response.status_code == 201
    # resolve + authorize_deployment share one memoized decision per request
    assert [(s.permission, s.obj, s.min_visible, s.legacy) for s in samples] == [
        ("can_deploy", f"agent:{agent.id}", "can_deploy", Verdict.ALLOW)
    ]


async def test_legacy_mode_records_nothing(client, db, install) -> None:
    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    samples: list[ShadowSample] = []
    install(_service("legacy", samples=samples))
    response = await client.post(
        "/v1/threads",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={"agent_id": deployment.public_id},
    )
    assert response.status_code == 201 and samples == []


async def test_deployment_and_key_management_require_can_deploy(client, db, install) -> None:
    agent = await _seed_agent(db)
    install(_service("enforce", {(USER, "can_run", f"agent:{agent.id}")}))
    assert (await client.get("/api/agent-api/deployment-candidates")).json() == []
    forbidden = await client.post("/api/agent-api/deployments", json={"agent_id": str(agent.id)})
    assert forbidden.status_code == 403 and _code(forbidden) == "permission_required"
    install(_service("enforce"))
    hidden = await client.post("/api/agent-api/deployments", json={"agent_id": str(agent.id)})
    assert hidden.status_code == 404 and _code(hidden) == "AGENT_NOT_FOUND"
    install(_service("enforce", _deployer(agent.id)))
    candidates = await client.get("/api/agent-api/deployment-candidates")
    assert [row["agent_id"] for row in candidates.json()] == [str(agent.id)]
    created = await client.post("/api/agent-api/deployments", json={"agent_id": str(agent.id)})
    assert created.status_code == 201
    deployment_id = created.json()["id"]
    key = {"name": "k", "scopes": ["invoke"], "deployment_ids": [deployment_id]}
    install(_service("enforce"))
    refused = await client.post("/api/agent-api/keys", json=key)
    assert refused.status_code == 422 and _code(refused) == "AGENT_API_DEPLOYMENT_NOT_FOUND"
    patch = await client.patch(
        f"/api/agent-api/deployments/{deployment_id}", json={"status": "disabled"}
    )
    assert patch.status_code == 404 and _code(patch) == "AGENT_DEPLOYMENT_NOT_FOUND"
    assert (await client.get("/api/agent-api/keys")).json() == []  # PRIVATE reads unaffected
    install(_service("enforce", _deployer(agent.id)))
    assert (await client.post("/api/agent-api/keys", json=key)).status_code == 201
