"""D-09 matrix: tools, MCP servers, credentials and skills (plan 9.4, 9.5, 8.4)."""

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.creation import CredentialCreation
from tests.authz.d04_support import add_server, add_tool, seed_scope
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
)
from tests.authz.skill_route_testkit import _skill
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.conftest import TEST_USER_ID, TestSession

__all__ = ["matrix"]  # fixture re-export
Seed = Callable[[], Awaitable[Target]]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _tool() -> Target:
    async with TestSession() as db:
        tool = await add_tool(db)
    return Target("tools", "tool", tool.id, {"tool_id": str(tool.id)})


async def _server() -> Target:
    async with TestSession() as db:
        server, _ = await add_server(db)
    return Target("mcp_servers", "mcp_server", server.id, {"server_id": str(server.id)})


async def _credential() -> Target:
    async with TestSession() as db:
        creation = CredentialCreation(definition_key="http_bearer", name="c", data={"token": "x"})
        row = await create_test_credential(db, creation, unit_context(TEST_USER_ID))
        await db.commit()
    path = {"credential_id": str(row.id)}
    return Target("credentials", "credential", row.id, path, grant_relation="consumer")


async def _skill_target() -> Target:
    skill_id = await _skill(TEST_USER_ID)
    return Target("skills", "skill", skill_id, {"skill_id": str(skill_id)})


SEEDS: dict[str, Seed] = {
    "tool": _tool,
    "mcp_server": _server,
    "credential": _credential,
    "skill": _skill_target,
}
# D-03: enforce resolves skill rows from DB candidates first, so a never-shared row is the
# existing 404 without reaching OpenFGA (still fail-closed: nothing is granted).
SKILL_PREFILTER = {("stranger", "down"): 404}
# Documented deferrals: service-level private writes still require ownership, so a
# granted editor passes the route check and gets the service's 404 (progress D-03 ->
# F-13, D-04 -> F-05/F-06).
OWNER_ONLY_WRITE = {("granted", "enforce"): 404}
RES_PROBES: tuple[tuple[str, Probe], ...] = (
    ("tool", Probe("GET /api/tools/{tool_id}")),
    ("tool", Probe("PATCH /api/tools/{tool_id}", {"name": "renamed"})),
    ("mcp_server", Probe("GET /api/mcp-servers/{server_id}")),
    ("mcp_server", Probe("PATCH /api/mcp-servers/{server_id}", {"name": "renamed"})),
    ("credential", Probe("GET /api/credentials/{credential_id}")),
    (
        "credential",
        Probe("PATCH /api/credentials/{credential_id}", {"name": "x"}, known=OWNER_ONLY_WRITE),
    ),
    ("skill", Probe("GET /api/skills/{skill_id}", known=SKILL_PREFILTER)),
    (
        "skill",
        Probe(
            "PATCH /api/skills/{skill_id}",
            {"description": "renamed"},
            known=SKILL_PREFILTER | OWNER_ONLY_WRITE,
        ),
    ),
)
LISTS = (
    ("tool", "GET /api/tools"),
    ("mcp_server", "GET /api/mcp-servers"),
    ("credential", "GET /api/credentials"),
    ("skill", "GET /api/skills"),
)


@pytest.mark.parametrize(
    ("kind", "probe", "actor", "mode"),
    [(kind, *case) for kind, probe in RES_PROBES for case in cases((probe,))],
    ids=str,
)
async def test_resource_routes_follow_the_8_4_table(
    client: AsyncClient, matrix: Matrix, kind: str, probe: Probe, actor: Actor, mode: Mode
) -> None:
    await matrix.check(client, probe, actor, mode, await SEEDS[kind]())


@pytest.mark.parametrize(
    ("kind", "route", "actor", "mode"),
    [(kind, route, actor, mode) for kind, route in LISTS for actor in ACTORS for mode in MODES],
    ids=str,
)
async def test_list_pages_show_only_confirmed_candidates(
    client: AsyncClient, matrix: Matrix, kind: str, route: str, actor: Actor, mode: Mode
) -> None:
    await matrix.check_list(client, route, actor, mode, await SEEDS[kind]())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("held", [False, True])
async def test_create_requires_the_org_capability(
    client: AsyncClient, matrix: Matrix, mode: Mode, held: bool
) -> None:
    probe = Probe(
        "POST /api/tools", {"definition_key": "http_request", "name": "n", "parameters": {}}
    )
    matrix.use(mode, {("user:" + str(TEST_USER_ID), "cap_create_tool", ORG)} if held else None)
    response = await matrix.call(client, probe)
    if mode == "down":
        assert_contract(response, 503)
    elif mode == "enforce" and not held:
        assert_contract(response, 403)
        assert response.json()["error"]["details"] == {"capability": "cap_create_tool"}
    else:
        assert response.status_code == 201, response.text


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("super_user", [False, True])
@pytest.mark.parametrize(
    "route", ["GET /api/system-credentials", "GET /api/system-llm-settings", "GET /api/tool-types"]
)
async def test_super_and_catalog_routes_never_consult_openfga(
    client: AsyncClient, matrix: Matrix, route: str, super_user: bool, mode: Mode
) -> None:
    matrix.as_user(super_user=super_user)
    matrix.use(mode)
    response = await matrix.call(client, Probe(route))
    operator_only = "/tool-types" not in route
    assert_contract(response, 403 if operator_only and not super_user else 200)
