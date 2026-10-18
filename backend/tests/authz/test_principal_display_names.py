"""Principal names shown by the share access list (S16) and role grants (S12).

A user is named by their profile ``display_name`` (registration ``name`` only as a
fallback), groups by their name — the screens must never fall back to raw ids.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization_group import Group
from app.models.user import User
from tests.authz.e01_support import TEST_USER_ID, add_tool, base, seed
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def _rename(db: AsyncSession, user_id: object, display_name: str) -> None:
    await db.execute(update(User).where(User.id == user_id).values(display_name=display_name))
    await db.commit()


async def test_access_list_names_the_owner_by_profile_name(
    client: AsyncClient, db: AsyncSession
) -> None:
    await seed(db)
    tool = await add_tool(db)
    await _rename(db, TEST_USER_ID, "김지원")
    response = await client.get(f"{base('tool', tool.id)}/access")
    assert response.status_code == 200
    owner = response.json()["items"][0]
    assert owner["source"] == "owner"
    assert owner["principal"]["display_name"] == "김지원"


async def test_role_and_capability_grants_carry_principal_names(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    await grant_admin(db, org_fixture)
    await _rename(db, org_fixture.user.id, "김지원")
    group = Group(org_id=org_fixture.first, tenant_id=org_fixture.tenant, name="플랫폼팀")
    db.add(group)
    await db.commit()
    org = f"/api/orgs/{org_fixture.first}"
    original = await raw_client.get(f"{org}/capabilities")
    assigned = await raw_client.put(
        f"{org}/capabilities",
        json={
            "principal": {"type": "group", "id": str(group.id)},
            "capabilities": ["cap_create_agent"],
        },
        headers={**org_fixture.session.headers(), "If-Match": original.headers["etag"]},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["items"][0]["principal_name"] == "플랫폼팀"
    roles = (await raw_client.get(f"{org}/roles")).json()["items"]
    names = {row["principal"]["id"]: row["principal_name"] for row in roles}
    assert names[str(org_fixture.user.id)] == "김지원"
    assert all(names.values())  # seeded groups are named too
    capabilities = (await raw_client.get(f"{org}/capabilities")).json()["items"]
    named = {row["principal"]["id"]: row["principal_name"] for row in capabilities}
    assert named[str(group.id)] == "플랫폼팀"
