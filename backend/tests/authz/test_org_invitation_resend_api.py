"""Resending an invitation replaces old links and is idempotent for that original row."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization_group import OrgInvitation
from tests.authz.test_org_groups_api import grant_admin
from tests.conftest import register_session

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_resend_returns_new_link_and_old_link_cannot_join(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: an organization admin's original invitation link.
    await grant_admin(db, org_fixture)
    path = f"/api/orgs/{org_fixture.first}/invitations"
    original = await raw_client.post(
        path,
        json={"email": "resend@example.com", "role": "builder"},
        headers=org_fixture.session.headers(),
    )
    key = UUID(original.json()["id"])
    # When: the admin reissues this invitation.
    replacement = await raw_client.post(
        f"{path}/{key}/resend", headers=org_fixture.session.headers()
    )
    # Then: a new row/token replaces the original, and retries return that replacement.
    assert replacement.status_code == 201
    assert replacement.json()["id"] != str(key)
    assert replacement.json()["token"] != original.json()["token"]
    repeated = await raw_client.post(f"{path}/{key}/resend", headers=org_fixture.session.headers())
    assert repeated.status_code == 200
    assert repeated.json()["token"] == replacement.json()["token"]
    row = await db.get(OrgInvitation, key)
    assert row is not None and row.revoked_at is not None
    recipient = await register_session(raw_client, email="resend@example.com", name="Recipient")
    old = await raw_client.post(
        "/api/invitations/accept",
        json={"token": original.json()["token"]},
        headers=recipient.headers(),
    )
    assert old.status_code == 404
    current = await raw_client.post(
        "/api/invitations/accept",
        json={"token": replacement.json()["token"]},
        headers=recipient.headers(),
    )
    assert current.status_code == 200


async def test_resend_requires_member_management_and_csrf(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: an invitation and an organization member without management rights.
    await grant_admin(db, org_fixture)
    path = f"/api/orgs/{org_fixture.first}/invitations"
    created = await raw_client.post(
        path, json={"email": "restricted@example.com"}, headers=org_fixture.session.headers()
    )
    recipient = await register_session(raw_client, email="restricted@example.com", name="Recipient")
    # When: the member attempts to resend a manager's invitation.
    result = await raw_client.post(
        f"{path}/{created.json()['id']}/resend", headers=recipient.headers()
    )
    no_csrf = await raw_client.post(f"{path}/{created.json()['id']}/resend")
    # Then: neither operation revokes the pending invitation.
    assert result.status_code == 404
    assert no_csrf.status_code == 403
    row = await db.get(OrgInvitation, UUID(created.json()["id"]))
    assert row is not None and row.revoked_at is None
