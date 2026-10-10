"""Signup honors default role and cannot leave an account after a late membership failure."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization import OrganizationRoleGrant, TenantRoleGrant
from app.models.organization_group import Group, OrgInvitation
from app.models.user import User
from tests.authz.registration_fixture import seed_registration_default
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_default_builder_policy_grants_builder_without_operator_or_company_admin(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    # Given: the default org grants builder to new members and first-admin promotion is off.
    organization = await seed_registration_default(db)
    organization.settings = {"default_member_role": "builder"}
    await db.commit()
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "allow_first_user_as_admin", False)
        # When: the account signs up.
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "new-builder@example.com",
                "password": "correct horse",
                "name": "Builder",
            },
        )
    # Then: the org role grants creation capability without company/platform authority.
    assert result.status_code == 201
    user_id = UUID(result.json()["user"]["id"])
    assert result.json()["user"]["is_super_user"] is False
    assert (
        await db.scalar(
            select(OrganizationRoleGrant.role).where(OrganizationRoleGrant.principal_id == user_id)
        )
        == "builder"
    )
    assert (
        await db.scalar(select(TenantRoleGrant.role).where(TenantRoleGrant.user_id == user_id))
        is None
    )
    listing = await raw_client.get("/api/me/organizations")
    assert listing.status_code == 200
    assert "cap_create_agent" in listing.json()[0]["capabilities"]


async def test_group_removed_after_issuance_rolls_back_new_account_and_invitation_consumption(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: an invitation referring to a group which is later removed.
    await grant_admin(db, org_fixture)
    group = await db.scalar(select(Group).where(Group.org_id == org_fixture.first))
    assert group is not None
    created = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/invitations",
        json={"email": "late-failure@example.com", "role": "builder", "group_ids": [str(group.id)]},
        headers=org_fixture.session.headers(),
    )
    assert created.status_code == 201
    await db.delete(group)
    await db.commit()
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "registration_policy", "invite_only")
        # When: signup creates the account but the invitation's group preflight fails.
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "late-failure@example.com",
                "password": "correct horse",
                "name": "Failure",
                "invitation_token": created.json()["token"],
            },
        )
    # Then: the flushed account is rolled back and the link is not consumed.
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "invitation_group_unavailable"
    assert await db.scalar(select(User.id).where(User.email == "late-failure@example.com")) is None
    row = await db.get(OrgInvitation, UUID(created.json()["id"]))
    assert row is not None and row.accepted_at is None


async def test_closed_registration_refuses_even_a_valid_invitation(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: a valid invitation issued before account signup was closed.
    await grant_admin(db, org_fixture)
    created = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/invitations",
        json={"email": "closed-valid-link@example.com"},
        headers=org_fixture.session.headers(),
    )
    assert created.status_code == 201
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "registration_policy", "closed")
        # When: the invited email attempts account creation.
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "closed-valid-link@example.com",
                "password": "correct horse",
                "name": "Closed",
                "invitation_token": created.json()["token"],
            },
        )
    # Then: the invitation cannot reopen closed account creation or consume the link.
    assert result.status_code == 403
    assert (
        await db.scalar(select(User.id).where(User.email == "closed-valid-link@example.com"))
        is None
    )
    row = await db.get(OrgInvitation, UUID(created.json()["id"]))
    assert row is not None and row.accepted_at is None
