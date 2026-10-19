"""Membership, saved preference and explicit cookie boundary regressions."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import resolve_org_context
from app.exceptions import AppError
from app.models.organization import OrganizationMember, Tenant, TenantMember
from app.models.user import User

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_saved_preference_is_used_when_cookie_is_absent(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: a valid last-active organization preference.
    # When: a request has no current-organization cookie.
    context = await resolve_org_context(db, org_fixture.user, cookie=None)
    # Then: the preference supplies both company and organization.
    assert context.org_id == org_fixture.first and context.tenant_id == org_fixture.tenant


@pytest.mark.parametrize("cookie", ["malformed", "foreign"])
async def test_explicit_invalid_cookie_is_denied_instead_of_silently_falling_back(
    db: AsyncSession,
    org_fixture: OrgFixture,
    cookie: str,
) -> None:
    # Given: an invalid explicit selection despite another valid membership.
    selected = str(org_fixture.foreign) if cookie == "foreign" else cookie
    # When: the explicit cookie is resolved.
    with pytest.raises(AppError) as error:
        await resolve_org_context(db, org_fixture.user, cookie=selected)
    # Then: the user cannot turn a forged cookie into a usable context.
    assert error.value.status == 403 and error.value.code == "forbidden"


async def test_membership_removal_is_visible_on_the_next_resolution(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: a successful prior resolution, followed by removal committed elsewhere.
    await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.first))
    member = await db.get(OrganizationMember, (org_fixture.first, org_fixture.user.id))
    assert member is not None
    member.status = "removed"
    await db.commit()
    # When: the same cookie is reused on the next request.
    with pytest.raises(AppError) as error:
        await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.first))
    # Then: a prior successful context does not cache authorization for the removed member.
    assert error.value.status == 403


async def test_stale_saved_preference_falls_back_to_an_active_membership(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: the saved organization membership is removed, but B remains active.
    member = await db.get(OrganizationMember, (org_fixture.first, org_fixture.user.id))
    assert member is not None
    member.status = "removed"
    await db.commit()
    # When: no explicit cookie is supplied.
    context = await resolve_org_context(db, org_fixture.user, cookie=None)
    # Then: only an existing active membership can replace the stale preference.
    assert context.org_id == org_fixture.second


async def test_company_removal_blocks_context_even_when_org_member_remains_active(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: the company member is removed while its organization row remains active.
    member = await db.get(TenantMember, (org_fixture.tenant, org_fixture.user.id))
    assert member is not None
    member.status = "removed"
    await db.commit()
    # When: the organization's cookie is resolved.
    with pytest.raises(AppError) as error:
        await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.first))
    # Then: organization membership alone cannot bypass the company boundary.
    assert error.value.status == 403


async def test_company_suspension_uses_the_specific_suspension_error(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: a member's current company is suspended.
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    tenant.status = "suspended"
    await db.commit()
    # When: the current context is resolved.
    with pytest.raises(AppError) as error:
        await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.first))
    # Then: the product can render its S02 recovery state without changing companies.
    assert error.value.status == 403 and error.value.code == "tenant_suspended"


async def test_suspended_only_company_without_preference_reports_suspension(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: the member's only remaining company is suspended and no organization is saved.
    tenant = await db.get(Tenant, org_fixture.tenant)
    other = await db.get(OrganizationMember, (org_fixture.second, org_fixture.user.id))
    user = await db.get(User, org_fixture.user.id)
    assert tenant is not None and other is not None and user is not None
    tenant.status = "suspended"
    other.status = "removed"
    user.last_active_org_id = None
    await db.commit()
    # When: a request without a cookie falls back to "any active membership".
    with pytest.raises(AppError) as error:
        await resolve_org_context(db, org_fixture.user, cookie=None)
    # Then: plan 8.1 — every request of a suspended company is 403 tenant_suspended.
    assert error.value.status == 403 and error.value.code == "tenant_suspended"


async def test_platform_operator_cannot_select_a_foreign_organization(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: an operator principal without membership in the selected organization.
    operator = replace(org_fixture.user, is_super_user=True)
    # When: the explicit foreign organization cookie is resolved.
    with pytest.raises(AppError) as error:
        await resolve_org_context(db, operator, cookie=str(org_fixture.foreign))
    # Then: platform authority does not grant access to another company's content.
    assert error.value.status == 403


async def test_no_remaining_membership_returns_no_organization_conflict(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    # Given: all organization memberships are removed.
    for org in (org_fixture.first, org_fixture.second):
        member = await db.get(OrganizationMember, (org, org_fixture.user.id))
        assert member is not None
        member.status = "removed"
    user = await db.get(User, org_fixture.user.id)
    assert user is not None
    user.last_active_org_id = None
    await db.commit()
    # When: a request asks for a context without an explicit cookie.
    with pytest.raises(AppError) as error:
        await resolve_org_context(db, org_fixture.user, cookie=None)
    # Then: absence of usable membership is distinct from a forged selection.
    assert error.value.status == 409 and error.value.code == "no_organization"
