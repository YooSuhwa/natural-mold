"""Invitation acceptance rejects stale organization membership and group state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import OrganizationMember, OrganizationRoleGrant, TenantMember
from app.models.organization_group import Group, OrgInvitation
from tests.authz.org_invitation_accept_fixture import pending_invitation

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


@pytest.mark.parametrize(
    "state", ["company_member", "company_removed", "org_member", "deleted_group", "old_removed"]
)
async def test_acceptance_cannot_restore_blocked_membership_or_stale_groups(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    state: Literal[
        "company_member", "company_removed", "org_member", "deleted_group", "old_removed"
    ],
) -> None:
    # Given: a pending link whose membership/group context has changed since issuance.
    invitation = await pending_invitation(db, raw_client, org_fixture)
    expected = 403
    match state:
        case "company_member":
            db.add(
                TenantMember(
                    tenant_id=org_fixture.tenant,
                    user_id=invitation.recipient_id,
                    status="suspended",
                )
            )
        case "company_removed":
            db.add(
                TenantMember(
                    tenant_id=org_fixture.tenant,
                    user_id=invitation.recipient_id,
                    status="removed",
                )
            )
        case "org_member":
            db.add(
                OrganizationMember(
                    org_id=org_fixture.first,
                    tenant_id=org_fixture.tenant,
                    user_id=invitation.recipient_id,
                    status="suspended",
                )
            )
        case "deleted_group":
            group = await db.get(Group, invitation.group_id)
            assert group is not None
            await db.delete(group)
            expected = 409
        case "old_removed":
            db.add(
                OrganizationMember(
                    org_id=org_fixture.first,
                    tenant_id=org_fixture.tenant,
                    user_id=invitation.recipient_id,
                    status="removed",
                    removed_at=datetime.now(UTC),
                )
            )
            expected = 404
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    # When: the old link is presented.
    result = await raw_client.post(
        "/api/invitations/accept",
        json={"token": invitation.token},
        headers=invitation.session.headers(),
    )
    # Then: no consumption or new grant is persisted.
    assert result.status_code == expected
    row = await db.get(OrgInvitation, invitation.id)
    assert row is not None and row.accepted_at is None
    grants = list(
        (
            await db.scalars(
                select(OrganizationRoleGrant).where(
                    OrganizationRoleGrant.principal_id == invitation.recipient_id
                )
            )
        ).all()
    )
    assert grants == []
