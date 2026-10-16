"""B-03 exercises real authentication for self organization endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.config import settings
from app.models.user import User
from tests.authz.registration_fixture import clear_default_membership
from tests.conftest import register_session

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_authenticated_user_without_memberships_sees_empty_org_list(
    raw_client: AsyncClient,
    db: AsyncSession,
) -> None:
    # Given: an authenticated user without an organization membership.
    session = await register_session(raw_client, email="no-org@example.com", name="No organization")
    from uuid import UUID

    await clear_default_membership(db, UUID(session.body["user"]["id"]))
    # When: the user's organization list is requested.
    response = await raw_client.get("/api/me/organizations")
    # Then: the self endpoint responds with an empty list, without creating membership.
    assert response.status_code == 200
    assert response.json() == []


async def test_list_includes_only_current_company_and_org_memberships(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: a member of A/B, plus a foreign organization and group-derived role.
    # When: the self navigation list is read through real cookie authentication.
    response = await raw_client.get("/api/me/organizations")
    # Then: company/role/capability summaries contain only the member's organizations.
    assert response.status_code == 200
    rows = response.json()
    assert {row["id"] for row in rows} == {str(org_fixture.first), str(org_fixture.second)}
    first = next(row for row in rows if row["id"] == str(org_fixture.first))
    assert first["tenant_id"] == str(org_fixture.tenant)
    assert first["roles"] == ["builder"]
    assert set(first["capabilities"]) == {
        "cap_create_agent",
        "cap_create_skill",
        "cap_create_tool",
        "cap_register_mcp",
        "cap_publish_marketplace",
        "cap_connect_database",
    }


async def test_list_marks_exactly_the_org_the_cookie_resolves_to(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: the member switches to the second organization.
    switched = await raw_client.post(
        "/api/me/active-org",
        json={"org_id": str(org_fixture.second)},
        headers=org_fixture.session.headers(),
    )
    assert switched.status_code == 200
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(org_fixture.second))
    # When: the self navigation list is read with that cookie.
    rows = (await raw_client.get("/api/me/organizations")).json()
    # Then: only the selected organization is flagged active.
    assert {row["id"]: row["is_active"] for row in rows} == {
        str(org_fixture.first): False,
        str(org_fixture.second): True,
    }
    # And: without a cookie the saved preference (last_active_org_id) is the active org.
    raw_client.cookies.delete(ACTIVE_ORG_COOKIE)
    fallback = (await raw_client.get("/api/me/organizations")).json()
    assert {row["id"]: row["is_active"] for row in fallback} == {
        str(org_fixture.first): False,
        str(org_fixture.second): True,
    }
    # And: a cookie the user cannot use marks nothing active instead of failing the list.
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(uuid4()))
    response = await raw_client.get("/api/me/organizations")
    assert response.status_code == 200
    assert not any(row["is_active"] for row in response.json())


async def test_switch_requires_csrf_before_target_lookup(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: an authenticated member without a CSRF request header.
    # When: a nonexistent organization is selected.
    response = await raw_client.post("/api/me/active-org", json={"org_id": str(uuid4())})
    # Then: CSRF fails before lookup can reveal whether the target exists.
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_mismatch"


async def test_foreign_and_missing_switch_targets_have_identical_responses(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: a foreign organization and an unknown ID, neither usable by this user.
    # When: the authenticated switch endpoint receives each target.
    responses = [
        await raw_client.post(
            "/api/me/active-org",
            json={"org_id": str(target)},
            headers=org_fixture.session.headers(),
        )
        for target in (org_fixture.foreign, uuid4())
    ]
    # Then: both collapse to the same response and issue no organization cookie.
    assert [response.status_code for response in responses] == [404, 404]
    assert responses[0].json() == responses[1].json()
    assert all(ACTIVE_ORG_COOKIE not in response.cookies for response in responses)


async def test_valid_switch_persists_preference_and_sets_http_only_cookie(
    raw_client: AsyncClient,
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: a valid member selecting a different company/organization.
    # When: the CSRF-authenticated switch commits.
    response = await raw_client.post(
        "/api/me/active-org",
        json={"org_id": str(org_fixture.second)},
        headers=org_fixture.session.headers(),
    )
    # Then: the preference and cookie agree; auth cookie transport rules apply.
    assert response.status_code == 200
    assert response.json()["org_id"] == str(org_fixture.second)
    assert response.cookies[ACTIVE_ORG_COOKIE] == str(org_fixture.second)
    cookie = next(
        value for value in response.headers.get_list("set-cookie") if ACTIVE_ORG_COOKIE in value
    )
    assert "HttpOnly" in cookie and "Path=/" in cookie
    assert f"SameSite={settings.cookie_samesite}" in cookie
    user = await db.get(User, org_fixture.user.id, populate_existing=True)
    assert user is not None and user.last_active_org_id == org_fixture.second


async def test_logout_expires_current_organization_cookie(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: a browser retaining a previously selected current organization.
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(org_fixture.first))
    # When: the authenticated user logs out.
    response = await raw_client.post("/api/auth/logout", headers=org_fixture.session.headers())
    # Then: the server clears the organization context along with auth cookies.
    cookie = next(
        value for value in response.headers.get_list("set-cookie") if ACTIVE_ORG_COOKIE in value
    )
    assert "Max-Age=0" in cookie
