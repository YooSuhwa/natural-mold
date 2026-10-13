"""Authenticated company invitation acceptance behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.models.audit_event import AuditEvent
from app.models.organization import OrganizationMember, TenantMember, TenantRoleGrant
from app.models.tenant_invitation import TenantInvitation
from tests.authz.tenant_invitation_fixture import (
    pending_tenant_invitation,
    seed_tenant_invitation,
)
from tests.conftest import register_session

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_company_invitation_accepts_without_changing_active_org(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    invitation = await pending_tenant_invitation(
        db,
        raw_client,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
    )
    active_before = raw_client.cookies.get(ACTIVE_ORG_COOKIE)

    result = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )

    assert result.status_code == 200
    assert result.json() == {
        "org_id": None,
        "tenant_id": str(org_fixture.tenant),
        "user_id": str(invitation.recipient_id),
    }
    assert ACTIVE_ORG_COOKIE not in result.cookies
    assert raw_client.cookies.get(ACTIVE_ORG_COOKIE) == active_before
    member = await db.get(TenantMember, (org_fixture.tenant, invitation.recipient_id))
    row = await db.get(TenantInvitation, invitation.id)
    role = await db.scalar(
        select(TenantRoleGrant).where(
            TenantRoleGrant.tenant_id == org_fixture.tenant,
            TenantRoleGrant.user_id == invitation.recipient_id,
            TenantRoleGrant.role == "admin",
        )
    )
    assert member is not None and member.status == "active"
    assert row is not None and row.status == "accepted"
    assert role is not None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.tenant_id == org_fixture.tenant,
                OrganizationMember.user_id == invitation.recipient_id,
            )
        )
        == 0
    )
    events = list(
        (
            await db.scalars(
                select(AuditEvent).where(
                    AuditEvent.actor_user_id == invitation.recipient_id,
                    AuditEvent.action.in_(
                        [
                            "tenant.invitation_accept",
                            "tenant.member_add",
                            "tenant.role_grant",
                        ]
                    ),
                )
            )
        ).all()
    )
    actions = [
        event.action
        for event in events
        if event.event_metadata is not None
        and event.event_metadata.get("tenant_id") == str(org_fixture.tenant)
    ]
    assert sorted(actions) == [
        "tenant.invitation_accept",
        "tenant.member_add",
        "tenant.role_grant",
    ]
    replay = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "tenant_invitation_used"


async def test_wrong_email_expired_and_revoked_links_never_join_company(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    wrong_row, wrong_token = await seed_tenant_invitation(
        db,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email="bound-accept@example.com",
    )
    wrong = await register_session(raw_client, email="other-accept@example.com", name="Other")
    mismatch = await raw_client.post(
        "/api/invitations/accept",
        json={"token": wrong_token},
        headers=wrong.headers(),
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "tenant_invitation_email_mismatch"

    expired = await pending_tenant_invitation(
        db,
        raw_client,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email="expired-accept@example.com",
    )
    expired_row = await db.get(TenantInvitation, expired.id)
    assert expired_row is not None
    expired_row.created_at = datetime.now(UTC) - timedelta(days=8)
    expired_row.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db.commit()
    expired_result = await raw_client.post(
        "/api/invitations/accept",
        json={"token": expired.token},
        headers=expired.session.headers(),
    )
    assert expired_result.status_code == 410

    revoked = await pending_tenant_invitation(
        db,
        raw_client,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email="revoked-accept@example.com",
    )
    revoked_row = await db.get(TenantInvitation, revoked.id)
    assert revoked_row is not None
    revoked_row.status = "revoked"
    revoked_row.revoked_at = datetime.now(UTC)
    revoked_row.revoked_by = org_fixture.user.id
    await db.commit()
    revoked_result = await raw_client.post(
        "/api/invitations/accept",
        json={"token": revoked.token},
        headers=revoked.session.headers(),
    )
    assert revoked_result.status_code == 404
    assert revoked_result.json()["error"]["code"] == "tenant_invitation_revoked"
    await db.refresh(wrong_row)
    assert wrong_row.status == "pending"


async def test_removed_member_and_missing_csrf_leave_invitation_pending(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    invitation = await pending_tenant_invitation(
        db,
        raw_client,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email="removed-accept@example.com",
    )
    db.add(
        TenantMember(
            tenant_id=org_fixture.tenant,
            user_id=invitation.recipient_id,
            status="removed",
        )
    )
    await db.commit()
    blocked = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "invitation_membership_blocked"
    missing_csrf = await raw_client.post(
        "/api/invitations/accept", json={"token": invitation.token}
    )
    assert missing_csrf.status_code == 403
    row = await db.get(TenantInvitation, invitation.id)
    assert row is not None and row.status == "pending"


async def test_suspended_member_is_reactivated_by_a_fresh_invitation(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    invitation = await pending_tenant_invitation(
        db,
        raw_client,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email="suspended-accept@example.com",
        role=None,
    )
    db.add(
        TenantMember(
            tenant_id=org_fixture.tenant,
            user_id=invitation.recipient_id,
            status="suspended",
        )
    )
    await db.commit()
    result = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    assert result.status_code == 200
    member = await db.get(TenantMember, (org_fixture.tenant, invitation.recipient_id))
    assert member is not None and member.status == "active"
