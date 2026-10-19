"""D-09 matrix: KEY runtime API, deployment management and triggers (plan 9.1, 9.2, 11.6)."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import RES
from tests.authz.agent_route_fixture import seed_agent
from tests.authz.d04_support import seed_scope
from tests.authz.matrix_support import (
    ME,
    MODES,
    PRIVATE_ACTORS,
    Actor,
    Matrix,
    Mode,
    Probe,
    Target,
    assert_contract,
    cases,
    matrix,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_agent_runtime_public_api import _seed_deployed_agent_with_key

__all__ = ["matrix"]  # fixture re-export
DEPLOY = RES("agent", "can_deploy", param="agent_id", min_visible="can_run")
TRIGGER = {
    "trigger_type": "interval",
    "schedule_config": {"interval_minutes": 10},
    "input_message": "run",
}


async def _deployed(db: AsyncSession) -> tuple[Target, dict[str, str], str]:
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    await seed_scope(db)
    target = Target("agents", "agent", agent.id, {}, grant_relation="consumer")
    return target, {"Authorization": f"Bearer {cleartext}"}, deployment.public_id


# The key owner's agent stays put; OpenFGA alone varies. Keys on agents the owner does not
# own are deferred (progress D-06 -> F-02/F-08): moving the agent makes the key opaque 401.
KEY_HELD = {"deployer": ("can_run", "can_deploy"), "runner": ("can_run",), "none": ()}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("held", list(KEY_HELD))
async def test_runtime_key_follows_the_owners_can_deploy(
    client: AsyncClient, db: AsyncSession, matrix: Matrix, held: str, mode: Mode
) -> None:
    target, auth, public_id = await _deployed(db)
    matrix.use(mode, {(ME, perm, f"agent:{target.id}") for perm in KEY_HELD[held]})
    response = await matrix.call(
        client, Probe("POST /v1/threads", {"agent_id": public_id}), None, auth
    )
    status = 201 if mode == "legacy" or held == "deployer" else 404
    assert_contract(response, 503 if mode == "down" else status)


@pytest.mark.parametrize("mode", MODES)
async def test_runtime_rejects_unknown_keys_and_keeps_health_public(
    client: AsyncClient, db: AsyncSession, matrix: Matrix, mode: Mode
) -> None:
    _, _, public_id = await _deployed(db)
    matrix.use(mode)
    bogus = {"Authorization": "Bearer moldy_sk_bogus"}
    bad = await matrix.call(client, Probe("POST /v1/threads", {"agent_id": public_id}), None, bogus)
    assert (bad.status_code, bad.json()["error"]["code"]) == (401, "AGENT_API_KEY_INVALID")
    assert (await client.get("/v1/health")).status_code == 200


async def _agent_target() -> Target:
    async with TestSession() as db:
        await seed_scope(db)
    agent_id = await seed_agent(owner=TEST_USER_ID)
    return Target("agents", "agent", uuid.UUID(agent_id), {"agent_id": agent_id}, "consumer")


# Deployments on agents the caller does not own stay owner-scoped (D-06 -> F-02/F-08): the
# agent is loaded in the caller's private scope first, so every non-owner is the 404.
NOT_OWNED = {(a, m): 404 for a in ("granted", "visible", "stranger") for m in ("enforce", "down")}
CREATE = Probe("POST /api/agent-api/deployments", ok=201, known=NOT_OWNED, gate=DEPLOY)


@pytest.mark.parametrize(("probe", "actor", "mode"), cases((CREATE,)), ids=str)
async def test_deployment_create_rechecks_can_deploy(
    client: AsyncClient, matrix: Matrix, probe: Probe, actor: Actor, mode: Mode
) -> None:
    target = await _agent_target()
    probe = Probe(probe.route, {"agent_id": str(target.id)}, probe.ok, probe.known, probe.gate)
    await matrix.check(client, probe, actor, mode, target)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("actor", PRIVATE_ACTORS)  # shared agents: deferred as above
async def test_deployment_candidates_list_confirmed_agents(
    client: AsyncClient, matrix: Matrix, actor: Actor, mode: Mode
) -> None:
    target = await _agent_target()
    route = "GET /api/agent-api/deployment-candidates"
    await matrix.check_list(client, route, actor, mode, target, id_key="agent_id")


# PATCH re-checks the trigger's agent#can_run (9.1, held below); the run history does not.
TRIGGER_PROBES = (
    Probe("GET /api/triggers/{trigger_id}/runs"),
    Probe(
        "PATCH /api/triggers/{trigger_id}",
        {"input_message": "again"},
        known={("owner", "down"): 503},
    ),
)


@pytest.mark.parametrize(("probe", "actor", "mode"), cases(TRIGGER_PROBES, PRIVATE_ACTORS), ids=str)
async def test_trigger_rows_are_private(
    client: AsyncClient, matrix: Matrix, probe: Probe, actor: Actor, mode: Mode
) -> None:
    agent = await _agent_target()
    matrix.use("legacy")
    created = await client.post(f"/api/agents/{agent.path['agent_id']}/triggers", json=TRIGGER)
    assert created.status_code == 201, created.text
    trigger_id = created.json()["id"]
    target = Target(
        "agent_triggers", "agent_trigger", uuid.UUID(trigger_id), {"trigger_id": trigger_id}
    )
    run = {(ME, "can_run", f"agent:{agent.id}")}
    await matrix.check_private(client, probe, actor, mode, target, run)


async def test_private_key_reads_survive_an_openfga_outage(
    client: AsyncClient, db: AsyncSession, matrix: Matrix
) -> None:
    await _deployed(db)
    matrix.use("down")
    assert_contract(await client.get("/api/agent-api/keys"), 200)
    assert_contract(await client.get("/api/agent-api/deployments"), 200)
