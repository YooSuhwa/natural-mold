"""D-09 matrix: agent routes and the agent-scoped settings (plan 9.1, 9.3, 8.4)."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from tests.authz.agent_route_fixture import seed_agent
from tests.authz.d04_support import seed_scope
from tests.authz.matrix_support import (
    ACTORS,
    MODES,
    ORG,
    Actor,
    Matrix,
    Mode,
    Probe,
    Target,
    assert_contract,
    cases,
    matrix,
    place,
)
from tests.conftest import TEST_USER_ID, TestSession

__all__ = ["matrix"]  # fixture re-export


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _agent() -> Target:
    agent_id = await seed_agent(owner=TEST_USER_ID)
    path = {"agent_id": agent_id}
    return Target("agents", "agent", uuid.UUID(agent_id), path, grant_relation="consumer")


# Documented deferral: a granted editor passes the route gate but the service still
# loads the agent in the caller's private scope (progress D-02 -> F-02 for PUT), so the
# answer is the existing 404. Memory settings follow the table since F-09.
OWNER_ONLY = {("granted", "enforce"): 404}
PROBES = (
    Probe("GET /api/agents/{agent_id}"),
    Probe("PATCH /api/agents/{agent_id}/favorite", {"is_favorite": True}),
    Probe("PUT /api/agents/{agent_id}", {"name": "renamed"}, known=OWNER_ONLY),
    Probe("GET /api/agents/{agent_id}/memory-settings"),
    Probe("PATCH /api/agents/{agent_id}/memory-settings", {"memory_policy_override": "auto"}),
    Probe("GET /api/agents/{agent_id}/memo"),
    Probe("PUT /api/agents/{agent_id}/memo", {"content": "notes"}),
    Probe("GET /api/agents/{agent_id}/memo/proposals"),
    # the memo starts switched off, so an admitted proposal answers 409 (F-09)
    Probe("POST /api/agents/{agent_id}/memo/proposals", {"content": "idea"}, ok=409),
)


@pytest.mark.parametrize(("probe", "actor", "mode"), cases(PROBES), ids=str)
async def test_agent_routes_follow_the_8_4_table(
    client: AsyncClient, matrix: Matrix, probe: Probe, actor: Actor, mode: Mode
) -> None:
    await matrix.check(client, probe, actor, mode, await _agent())


@pytest.mark.parametrize("route", ["GET /api/agents", "GET /api/agents/summary"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("actor", ACTORS)
async def test_agent_lists_show_only_confirmed_candidates(
    client: AsyncClient, matrix: Matrix, route: str, actor: Actor, mode: Mode
) -> None:
    await matrix.check_list(client, route, actor, mode, await _agent())


@pytest.mark.parametrize("mode", MODES)
async def test_create_requires_cap_create_agent(
    client: AsyncClient, matrix: Matrix, mode: Mode
) -> None:
    target = await _agent()  # also seeds the model to bind
    async with TestSession() as db:
        agent = await db.get(Agent, target.id)
        assert agent is not None
        body = {"name": "n", "system_prompt": "p", "model_id": str(agent.model_id)}
    matrix.use(mode)  # enforce: the capability is not held
    response = await matrix.call(client, Probe("POST /api/agents", body))
    status = {"legacy": 201, "enforce": 403, "down": 503}[mode]
    assert_contract(response, status)
    if mode == "enforce":
        assert response.json()["error"]["details"] == {"capability": "cap_create_agent"}


@pytest.mark.parametrize("held", [False, True])
async def test_capability_tuple_unlocks_create(
    client: AsyncClient, matrix: Matrix, held: bool
) -> None:
    matrix.use("enforce", {(f"user:{TEST_USER_ID}", "cap_create_agent", ORG)} if held else None)
    body = {"name": "n", "system_prompt": "p", "model_id": str(uuid.uuid4())}
    response = await matrix.call(client, Probe("POST /api/agents", body))
    # held: past the capability gate, the unknown model is the next refusal
    assert response.status_code != 403 if held else response.status_code == 403


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("actor", ["owner", "stranger", "foreign"])
async def test_governance_archive_is_lifecycle_authority_not_openfga(
    client: AsyncClient, matrix: Matrix, actor: Actor, mode: Mode
) -> None:
    target = await _agent()
    await place(target, actor)
    matrix.use(mode)  # no tuples, even OpenFGA down: lifecycle authority is DB-based
    response = await matrix.call(client, Probe("DELETE /api/agents/{agent_id}"), target)
    assert_contract(response, 204 if actor == "owner" else 404)
