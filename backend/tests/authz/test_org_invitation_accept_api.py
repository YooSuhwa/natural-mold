"""A hash-only invitation binds the authenticated email to one company and organization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import OrganizationMember, OrganizationRoleGrant, Tenant, TenantMember
from app.models.organization_group import GroupMember, OrgInvitation
from tests.authz.org_invitation_accept_fixture import pending_invitation
from tests.conftest import register_session

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_acceptance_joins_company_org_group_role_and_selects_org_atomically(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: an existing authenticated account and a pending invitation with role/group.
    invitation = await pending_invitation(db, raw_client, org_fixture)
    # When: the invited account accepts its link.
    result = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    # Then: all membership facts and current selection are durable.
    assert result.status_code == 200
    assert result.json()["org_id"] == str(org_fixture.first)
    assert result.json()["tenant_id"] == str(org_fixture.tenant)
    assert result.cookies[ACTIVE_ORG_COOKIE] == str(org_fixture.first)
    member = await db.get(OrganizationMember, (org_fixture.first, invitation.recipient_id))
    company_member = await db.get(TenantMember, (org_fixture.tenant, invitation.recipient_id))
    row = await db.get(OrgInvitation, invitation.id)
    assert member is not None and member.status == "active"
    assert company_member is not None and company_member.status == "active"
    assert row is not None and row.accepted_by == invitation.recipient_id
    role = await db.scalar(
        select(OrganizationRoleGrant).where(
            OrganizationRoleGrant.org_id == org_fixture.first,
            OrganizationRoleGrant.principal_id == invitation.recipient_id,
            OrganizationRoleGrant.role == "builder",
        )
    )
    assert role is not None
    group_member = await db.get(GroupMember, (invitation.group_id, invitation.recipient_id))
    assert group_member is not None
    company_events = list(
        (
            await db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "tenant.member_add",
                    AuditEvent.actor_user_id == invitation.recipient_id,
                )
            )
        ).all()
    )
    assert (
        len(
            [
                event
                for event in company_events
                if event.event_metadata is not None
                and event.event_metadata.get("tenant_id") == str(org_fixture.tenant)
            ]
        )
        == 1
    )
    retried = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    assert retried.status_code == 409
    assert retried.json()["error"]["code"] == "invitation_used"


@pytest.mark.parametrize("state", ["expired", "revoked", "unknown"])
async def test_unusable_link_never_creates_membership(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    state: Literal["expired", "revoked", "unknown"],
) -> None:
    # Given: an unusable invitation link.
    invitation = await pending_invitation(db, raw_client, org_fixture)
    row = await db.get(OrgInvitation, invitation.id)
    assert row is not None
    token = invitation.token
    match state:
        case "expired":
            row.created_at = datetime.now(UTC) - timedelta(days=8)
            row.expires_at = datetime.now(UTC) - timedelta(days=1)
        case "revoked":
            row.revoked_at = datetime.now(UTC)
        case "unknown":
            token = "unknown-invitation-token"
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    # When: the invited account attempts acceptance.
    result = await raw_client.post(
        "/api/invitations/accept", json={"token": token}, headers=invitation.session.headers()
    )
    # Then: no member, company membership or consumption is committed.
    assert result.status_code == (410 if state == "expired" else 404)
    assert await db.get(OrganizationMember, (org_fixture.first, invitation.recipient_id)) is None
    assert await db.get(TenantMember, (org_fixture.tenant, invitation.recipient_id)) is None
    await db.refresh(row)
    assert row.accepted_at is None


async def test_other_email_cannot_accept_link_or_learn_target_email(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: a valid link presented by another authenticated account.
    invitation = await pending_invitation(db, raw_client, org_fixture)
    wrong = await register_session(raw_client, email="wrong@example.com", name="Wrong")
    # When: the other account attempts acceptance.
    result = await raw_client.post(
        "/api/invitations/accept", json={"token": invitation.token}, headers=wrong.headers()
    )
    # Then: the email binding is enforced without disclosing the invitation's email.
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "invitation_email_mismatch"
    assert "recipient@example.com" not in result.text


async def test_company_suspension_and_csrf_refuse_acceptance_before_mutation(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: a valid link to a suspended company.
    invitation = await pending_invitation(db, raw_client, org_fixture)
    company = await db.get(Tenant, org_fixture.tenant)
    assert company is not None
    company.status = "suspended"
    await db.commit()
    # When: the account submits acceptance with and without CSRF.
    denied = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    missing_csrf = await raw_client.post(
        "/api/invitations/accept", json={"token": invitation.token}
    )
    # Then: neither creates the membership, while authentication remains valid.
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "tenant_suspended"
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_mismatch"
    assert (await raw_client.get("/api/auth/me")).status_code == 200


async def test_invalid_token_length_never_echoes_the_secret_in_validation_details(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: an oversized secret which fails request parsing.
    invitation = await pending_invitation(db, raw_client, org_fixture)
    # When: the request is rejected by schema validation.
    result = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token + "x" * 300},
        headers=invitation.session.headers(),
    )
    # Then: the error identifies the field without copying the secret back to clients/logs.
    assert result.status_code == 422
    assert invitation.token not in result.text


async def test_existing_invitation_hash_survives_signing_secret_rotation(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: a pending link issued before the installation signing secret is rotated.
    invitation = await pending_invitation(db, raw_client, org_fixture)
    with pytest.MonkeyPatch.context() as rotation:
        rotation.setattr(settings, "jwt_secret", "dummy-new-signing-secret-for-acceptance-42")
        logged_in = await raw_client.post(
            "/api/auth/login", json={"email": "recipient@example.com", "password": "correct horse"}
        )
        assert logged_in.status_code == 200
        # When: the account authenticates under the new key and accepts the old link.
        result = await raw_client.post(
            "/api/invitations/accept",
            json={"token": invitation.token},
            headers={"X-CSRF-Token": logged_in.json()["csrf_token"]},
        )
    # Then: the stored hash, rather than the current MAC signer, proves link possession.
    assert result.status_code == 200
    row = await db.get(OrgInvitation, invitation.id)
    assert row is not None and row.accepted_by == invitation.recipient_id
