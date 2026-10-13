"""Native company invitation signup is company-only and transaction atomic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, NoReturn, assert_never
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.config import settings
from app.exceptions import AppError
from app.models.organization import OrganizationMember, Tenant, TenantMember, TenantRoleGrant
from app.models.tenant_invitation import TenantInvitation
from app.models.user import User
from app.services import registration_service
from app.services.tenants.invitation_acceptance import RegistrationTenantInvitationAcceptance
from tests.authz.tenant_invitation_native_fixture import (
    invitation_audit_count,
    native_company,
    seed_native_invitation,
)

if TYPE_CHECKING:
    from tests.authz.tenant_invitation_native_fixture import NativeInvitationDatabase

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_invitation_native_fixture"]


async def _invite_only(database: NativeInvitationDatabase, tenant_id: UUID) -> None:
    async with database.primary() as db:
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.settings = {"registration_policy": "invite_only"}
        await db.commit()


def _registration(email: str, token: str) -> dict[str, str]:
    return {
        "email": email,
        "password": "correct horse",
        "name": "Native Registration",
        "invitation_token": token,
    }


async def test_company_token_registration_commits_without_organization_enrollment(
    tenant_invitation_native_database: NativeInvitationDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tenant_invitation_native_database
    async with native_company(
        database.primary,
        invited_email="native-register@example.com",
        existing_invitee=False,
    ) as fixture:
        invitation, token = await seed_native_invitation(database.primary, fixture)
        await _invite_only(database, fixture.tenant_id)
        monkeypatch.setattr(settings, "allow_first_user_as_admin", False)
        monkeypatch.setattr(settings, "registration_policy", "invite_only")
        async with database.client() as client:
            result = await client.post(
                "/api/auth/register", json=_registration(fixture.invited_email, token)
            )
            assert result.status_code == 201
            created_user_id = UUID(result.json()["user"]["id"])
            fixture.owned.add_user(created_user_id)
            replay = await client.post(
                "/api/auth/register", json=_registration(fixture.invited_email, token)
            )
        assert replay.status_code == 409
        assert replay.json()["error"]["code"] == "tenant_invitation_used"
        assert ACTIVE_ORG_COOKIE not in result.cookies
        async with database.primary() as db:
            user = await db.get(User, created_user_id)
            assert user is not None and user.last_active_org_id is None
            member = await db.get(TenantMember, (fixture.tenant_id, created_user_id))
            role_count = await db.scalar(
                select(func.count())
                .select_from(TenantRoleGrant)
                .where(
                    TenantRoleGrant.tenant_id == fixture.tenant_id,
                    TenantRoleGrant.user_id == created_user_id,
                    TenantRoleGrant.role == "admin",
                )
            )
            org_count = await db.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(OrganizationMember.user_id == created_user_id)
            )
            row = await db.get(TenantInvitation, invitation.id)
        assert member is not None and member.status == "active"
        assert role_count == 1 and org_count == 0
        assert row is not None and row.status == "accepted" and row.accepted_at is not None
        assert (
            await invitation_audit_count(
                database.primary, fixture.tenant_id, "tenant.invitation_accept"
            )
            == 1
        )


@pytest.mark.parametrize("failure", ["wrong_email", "expired"])
async def test_invalid_company_token_registration_leaves_zero_user_or_membership(
    tenant_invitation_native_database: NativeInvitationDatabase,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["wrong_email", "expired"],
) -> None:
    database = tenant_invitation_native_database
    async with native_company(
        database.primary,
        invited_email=f"native-{failure}@example.com",
        existing_invitee=False,
    ) as fixture:
        invitation, token = await seed_native_invitation(database.primary, fixture)
        submitted_email = fixture.invited_email
        await _invite_only(database, fixture.tenant_id)
        match failure:
            case "wrong_email":
                submitted_email = "native-wrong-target@example.com"
                expected_status, expected_code = 403, "tenant_invitation_email_mismatch"
            case "expired":
                async with database.primary() as db:
                    row = await db.get(TenantInvitation, invitation.id)
                    assert row is not None
                    row.created_at = datetime.now(UTC) - timedelta(days=8)
                    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    await db.commit()
                expected_status, expected_code = 410, "tenant_invitation_expired"
            case unreachable:
                assert_never(unreachable)
        monkeypatch.setattr(settings, "allow_first_user_as_admin", False)
        monkeypatch.setattr(settings, "registration_policy", "invite_only")
        async with database.client() as client:
            result = await client.post(
                "/api/auth/register", json=_registration(submitted_email, token)
            )
        assert result.status_code == expected_status
        assert result.json()["error"]["code"] == expected_code
        async with database.primary() as db:
            assert await db.scalar(select(User.id).where(User.email == submitted_email)) is None
            member_count = await db.scalar(
                select(func.count())
                .select_from(TenantMember)
                .where(TenantMember.tenant_id == fixture.tenant_id)
            )
            row = await db.get(TenantInvitation, invitation.id)
        assert member_count == 1
        assert row is not None and row.status == "pending" and row.accepted_at is None


async def test_late_company_acceptance_failure_rolls_back_all_signup_facts(
    tenant_invitation_native_database: NativeInvitationDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tenant_invitation_native_database
    async with native_company(
        database.primary,
        invited_email="native-registration-rollback@example.com",
        existing_invitee=False,
    ) as fixture:
        invitation, token = await seed_native_invitation(database.primary, fixture)
        real_accept = registration_service.accept_registration_tenant_invitation

        async def fail_after_acceptance(
            attempt: RegistrationTenantInvitationAcceptance,
        ) -> NoReturn:
            await real_accept(attempt)
            raise AppError("conflict", "controlled late failure", 409)

        await _invite_only(database, fixture.tenant_id)
        monkeypatch.setattr(settings, "allow_first_user_as_admin", False)
        monkeypatch.setattr(settings, "registration_policy", "invite_only")
        monkeypatch.setattr(
            registration_service,
            "accept_registration_tenant_invitation",
            fail_after_acceptance,
        )
        async with database.client() as client:
            result = await client.post(
                "/api/auth/register", json=_registration(fixture.invited_email, token)
            )
        assert result.status_code == 409
        async with database.primary() as db:
            assert (
                await db.scalar(select(User.id).where(User.email == fixture.invited_email)) is None
            )
            role_count = await db.scalar(
                select(func.count())
                .select_from(TenantRoleGrant)
                .where(TenantRoleGrant.tenant_id == fixture.tenant_id)
            )
            row = await db.get(TenantInvitation, invitation.id)
        assert role_count == 1
        assert row is not None and row.status == "pending" and row.accepted_at is None
