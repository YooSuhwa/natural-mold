"""H-05: per-row ``my_permissions`` for tools/MCP/credentials/skills (plan 13.1, 13.9)."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool
from tests.authz.d04_support import (
    OTHER_ORG_ID,
    OTHER_USER_ID,
    AuthzSwitch,
    Unavailable,
    add_server,
    add_tool,
    allow,
    authz,
)
from tests.authz.e01_support import add_credential, add_skill, seed

__all__ = ["authz"]

ALL = ["can_use", "can_view", "can_edit", "can_share", "can_delete"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _path(kind: str, *ids: object) -> str:
    query = "&".join(f"ids={i}" for i in ids)
    return f"/api/resources/{kind}/permissions?{query}"


async def _answer(client: AsyncClient, kind: str, *ids: object) -> dict[str, list[str]]:
    response = await client.get(_path(kind, *ids))
    assert response.status_code == 200, response.text
    return {item["id"]: item["my_permissions"] for item in response.json()["items"]}


async def _system_tool(db: AsyncSession) -> Tool:
    tool = Tool(definition_key="http_request", name="sys", parameters={}, is_system=True)
    db.add(tool)
    await db.commit()
    return tool


async def test_legacy_answers_owner_and_platform_rows_and_omits_the_rest(
    client: AsyncClient, db: AsyncSession
) -> None:
    mine = await add_tool(db)
    theirs = await add_tool(db, owner=OTHER_USER_ID)
    foreign = await add_tool(db, org=OTHER_ORG_ID)
    system = await _system_tool(db)
    answer = await _answer(client, "tool", mine.id, theirs.id, foreign.id, system.id, uuid4())
    assert answer == {str(mine.id): ALL, str(system.id): ["can_use", "can_view"]}


async def test_enforce_reports_held_permissions_of_visible_rows_only(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    shared = await add_tool(db, owner=OTHER_USER_ID)
    hidden = await add_tool(db, owner=OTHER_USER_ID)
    obj = f"tool:{shared.id}"
    authz.use(allow(("can_use", obj), ("can_view", obj), ("can_view", f"tool:{hidden.id}")))
    assert await _answer(client, "tool", shared.id, hidden.id) == {
        str(shared.id): ["can_use", "can_view"]
    }


async def test_shadow_keeps_the_legacy_answer_and_samples(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    server, _ = await add_server(db)
    shared, _ = await add_server(db, owner=OTHER_USER_ID, name="other")
    authz.use(allow(("can_use", f"mcp_server:{shared.id}")), mode="shadow")
    assert await _answer(client, "mcp_server", server.id, shared.id) == {str(server.id): ALL}
    assert authz.samples


async def test_enforce_outage_is_503_authz_unavailable(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)
    authz.use(client=Unavailable())
    response = await client.get(_path("tool", tool.id))
    assert (response.status_code, response.json()["error"]["code"]) == (503, "authz_unavailable")


async def test_credentials_and_skills_use_their_own_vocabulary(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    credential = await add_credential(db, owner=OTHER_USER_ID)
    skill = await add_skill(db, owner=OTHER_USER_ID, slug="s-h05")
    authz.use(
        allow(
            ("can_use", f"credential:{credential.id}"),
            ("can_use", f"skill:{skill.id}"),
            ("can_edit", f"skill:{skill.id}"),
        )
    )
    assert await _answer(client, "credential", credential.id) == {str(credential.id): ["can_use"]}
    assert await _answer(client, "skill", skill.id) == {str(skill.id): ["can_use", "can_edit"]}


async def test_id_count_is_capped(client: AsyncClient) -> None:
    response = await client.get(_path("tool", *(uuid4() for _ in range(101))))
    assert response.status_code == 422


async def test_my_organizations_say_whether_capabilities_are_enforced(
    client: AsyncClient, authz: AuthzSwitch
) -> None:
    legacy = await client.get("/api/me/organizations")
    assert [org["capabilities_enforced"] for org in legacy.json()] == [False]
    authz.use()
    enforced = await client.get("/api/me/organizations")
    assert [org["capabilities_enforced"] for org in enforced.json()] == [True]


async def test_shadow_samples_only_the_first_rows(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    tools = [await add_tool(db, name=f"t{i}") for i in range(10)]
    authz.use(mode="shadow")
    answer = await _answer(client, "tool", *(t.id for t in tools))
    assert answer == {str(t.id): ALL for t in tools}
    # 3 visibility samples + 3 rows x 5 permissions, not 10 x 6.
    assert len(authz.samples) == 3 + 3 * len(ALL)
