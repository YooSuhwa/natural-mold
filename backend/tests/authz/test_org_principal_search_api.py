"""Principal search respects current org, bounded results and email privacy."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from app.rate_limit import limiter
from tests.authz.test_org_groups_api import grant_admin
from tests.conftest import register_session

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_email_search_is_exact_for_member_and_admin_can_search_partial_email(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    path = f"/api/orgs/{org_fixture.first}/principals"
    partial = await raw_client.get(path, params={"q": "org-context", "types": "user"})
    assert partial.status_code == 200 and partial.json() == []
    exact = await raw_client.get(path, params={"q": org_fixture.user.email, "types": "user"})
    assert exact.status_code == 200 and len(exact.json()) == 1
    assert exact.json()[0]["email"] is None
    await grant_admin(db, org_fixture)
    allowed = await raw_client.get(path, params={"q": "org-context", "types": "user"})
    assert allowed.status_code == 200
    assert allowed.json()[0]["email"] == org_fixture.user.email


async def test_search_requires_current_org_and_two_characters(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    other = await raw_client.get(
        f"/api/orgs/{org_fixture.second}/principals", params={"q": "Builders"}
    )
    assert other.status_code == 403
    short = await raw_client.get(f"/api/orgs/{org_fixture.first}/principals", params={"q": "B"})
    assert short.status_code == 422
    group = await raw_client.get(
        f"/api/orgs/{org_fixture.first}/principals", params={"q": "Builders", "types": "group"}
    )
    assert group.status_code == 200
    assert group.json()[0]["type"] == "group" and group.json()[0]["member_count"] == 1


async def test_search_returns_at_most_twenty_active_org_users(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    for index in range(24):
        user = User(email=f"principal-{index}@example.com", name=f"Sample member {index}")
        db.add(user)
        await db.flush()
        db.add(TenantMember(tenant_id=org_fixture.tenant, user_id=user.id))
        db.add(
            OrganizationMember(
                org_id=org_fixture.first, tenant_id=org_fixture.tenant, user_id=user.id
            )
        )
    await db.commit()
    found = await raw_client.get(
        f"/api/orgs/{org_fixture.first}/principals", params={"q": "Sample", "types": "user"}
    )
    assert found.status_code == 200 and len(found.json()) == 20


async def test_thirty_per_minute_limit_is_per_user_not_shared_browser_ip(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter.reset()
    monkeypatch.setattr(limiter, "enabled", True)
    path = f"/api/orgs/{org_fixture.first}/principals"
    try:
        results = [await raw_client.get(path, params={"q": "Builders"}) for _ in range(31)]
        assert [response.status_code for response in results[:30]] == [200] * 30
        assert results[30].status_code == 429
        other = await register_session(
            raw_client, email="second-principal-search@example.com", name="Second"
        )
        user_id = other.body["user"]["id"]
        from uuid import UUID

        parsed = UUID(user_id)
        db.add(TenantMember(tenant_id=org_fixture.tenant, user_id=parsed))
        db.add(
            OrganizationMember(
                org_id=org_fixture.first, tenant_id=org_fixture.tenant, user_id=parsed
            )
        )
        await db.commit()
        assert (await raw_client.get(path, params={"q": "Builders"})).status_code == 200
    finally:
        limiter.reset()
