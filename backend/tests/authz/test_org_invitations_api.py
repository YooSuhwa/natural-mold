"""Invitation creation is idempotent and persists only the token hash."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization_group import OrgInvitation
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_invitation_retry_returns_same_row_token_and_no_plaintext_storage(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    await grant_admin(db, org_fixture)
    path = f"/api/orgs/{org_fixture.first}/invitations"
    payload = {"email": "Invited@example.com", "role": "builder"}
    original = await raw_client.post(path, json=payload, headers=org_fixture.session.headers())
    assert original.status_code == 201
    token = original.json()["token"]
    assert isinstance(token, str) and len(token) >= 43
    row = await db.get(OrgInvitation, UUID(original.json()["id"]))
    assert row is not None
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.email == "invited@example.com"
    retried = await raw_client.post(path, json=payload, headers=org_fixture.session.headers())
    assert retried.status_code == 200
    assert retried.json()["id"] == original.json()["id"]
    assert retried.json()["token"] == token
    listed = await raw_client.get(path)
    assert listed.status_code == 200
    assert "token_hash" not in listed.text and token not in listed.text
    revoked = await raw_client.delete(f"{path}/{row.id}", headers=org_fixture.session.headers())
    assert revoked.status_code == 204
    await db.refresh(row)
    assert row.revoked_at is not None


async def test_invitation_groups_must_belong_to_org_and_member_cannot_invite(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    path = f"/api/orgs/{org_fixture.first}/invitations"
    forbidden = await raw_client.post(
        path, json={"email": "invited@example.com"}, headers=org_fixture.session.headers()
    )
    assert forbidden.status_code == 403
    await grant_admin(db, org_fixture)
    invalid = await raw_client.post(
        path,
        json={"email": "invited@example.com", "group_ids": [str(uuid4())]},
        headers=org_fixture.session.headers(),
    )
    assert invalid.status_code == 404


async def test_invitation_creation_requires_csrf_before_org_lookup(
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    response = await raw_client.post(
        f"/api/orgs/{uuid4()}/invitations", json={"email": "invited@example.com"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_mismatch"


@pytest.mark.parametrize("state", ["expired", "accepted", "revoked"])
async def test_finished_invitation_is_never_reused_for_a_new_invite(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    state: str,
) -> None:
    # Given: a previous invitation which is no longer pending.
    await grant_admin(db, org_fixture)
    path = f"/api/orgs/{org_fixture.first}/invitations"
    payload = {"email": "previous@example.com"}
    original = await raw_client.post(path, json=payload, headers=org_fixture.session.headers())
    row = await db.get(OrgInvitation, UUID(original.json()["id"]))
    assert row is not None
    now = datetime.now(UTC)
    if state == "expired":
        row.created_at = now - timedelta(days=8)
        row.expires_at = now - timedelta(days=1)
    elif state == "accepted":
        row.accepted_at = now
    else:
        row.revoked_at = now
    await db.commit()
    # When: the organization invites the same address again.
    result = await raw_client.post(path, json=payload, headers=org_fixture.session.headers())
    # Then: the new link has its own hash and the old lifecycle state remains visible.
    assert result.status_code == 201
    assert result.json()["id"] != original.json()["id"]
    assert result.json()["token"] != original.json()["token"]
    listed = await raw_client.get(path)
    previous = next(item for item in listed.json()["items"] if item["id"] == str(row.id))
    assert previous["status"] == state


async def test_invitation_retry_after_signing_key_rotation_never_changes_stored_hash(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a pending invitation minted under the previous signing key.
    await grant_admin(db, org_fixture)
    path = f"/api/orgs/{org_fixture.first}/invitations"
    payload = {"email": "rotation@example.com"}
    original = await raw_client.post(path, json=payload, headers=org_fixture.session.headers())
    row = await db.get(OrgInvitation, UUID(original.json()["id"]))
    assert row is not None
    old_hash = row.token_hash
    # Only the invitation signer is changed; authentication still verifies the original JWT.
    import app.services.orgs.invitations as service

    original_signer = service.create_org_invitation_token

    def rotated_signer(invitation_id: UUID) -> str:
        with monkeypatch.context() as signer_patch:
            signer_patch.setattr(settings, "jwt_secret", "dummy-invitation-rotation-secret-42")
            return original_signer(invitation_id)

    monkeypatch.setattr(service, "create_org_invitation_token", rotated_signer)
    # When: an authorized caller retries after rotation.
    retried = await raw_client.post(path, json=payload, headers=org_fixture.session.headers())
    # Then: it returns metadata without substituting a link which cannot match the stored hash.
    assert retried.status_code == 200
    assert retried.json()["id"] == original.json()["id"]
    assert retried.json()["token"] is None
    await db.refresh(row)
    assert row.token_hash == old_hash
    assert old_hash == hashlib.sha256(original.json()["token"].encode()).hexdigest()


async def test_repeated_revocation_preserves_timestamp_and_scoped_lookup(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    # Given: an already revoked invitation.
    await grant_admin(db, org_fixture)
    path = f"/api/orgs/{org_fixture.first}/invitations"
    created = await raw_client.post(
        path, json={"email": "revoked@example.com"}, headers=org_fixture.session.headers()
    )
    key = UUID(created.json()["id"])
    first = await raw_client.delete(f"{path}/{key}", headers=org_fixture.session.headers())
    assert first.status_code == 204
    row = await db.get(OrgInvitation, key)
    assert row is not None
    timestamp = row.revoked_at
    # When: the same organization revokes again.
    repeated = await raw_client.delete(f"{path}/{key}", headers=org_fixture.session.headers())
    # Then: the response is idempotent and the first revocation time is retained.
    assert repeated.status_code == 204
    await db.refresh(row)
    assert row.revoked_at == timestamp
    foreign = await raw_client.delete(
        f"/api/orgs/{org_fixture.second}/invitations/{key}", headers=org_fixture.session.headers()
    )
    assert foreign.status_code == 403
