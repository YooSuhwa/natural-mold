"""Membership events queue their OpenFGA tuples without the nightly reconcile (7.2.1, 8.6).

Each test starts from the reconciled store, runs one membership route and drains the
outbox: the store must then equal ``expected_tuples()`` exactly, and every narrowing
leaves a finalized revocation marker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_fanout_backfill import backfill_catalog_fanout
from app.authz.service import AuthzService
from app.migrations.org_authz_v1.contracts import ORG, TENANT
from app.models.authz import ResourceGrant
from app.models.model import Model
from app.models.organization import TenantMember
from app.models.organization_group import Group
from app.models.user import User
from tests.authz.membership_sync_testkit import (
    MemberStore,
    assert_parity,
    drain,
    markers,
    pending_rows,
    reconciled_store,
    user_tuples,
)
from tests.authz.test_org_groups_api import grant_admin
from tests.conftest import TestSession, make_user, register_session

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_fresh_signup_sees_catalog_models_in_enforce_after_the_drain(
    db: AsyncSession,
    raw_client: AsyncClient,
    raw_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the default organization's catalog fanout is projected; OpenFGA then goes down.
    monkeypatch.setattr("app.database.async_session", TestSession)
    operator = await make_user(db, is_super_user=True)
    model = Model(provider="openai", model_name="visible", display_name="Visible")
    db.add(model)
    await db.commit()
    await backfill_catalog_fanout(TestSession, operator.id, tenant_id=TENANT)
    store = MemberStore()
    await reconciled_store(store)
    await drain(store)
    store.failing_objects = {f"organization:{ORG}", f"tenant:{TENANT}"}
    monkeypatch.setattr(raw_test_app.state, "authz", AuthzService(store, mode="enforce"), False)
    # When: a new account signs up into the default organization.
    await register_session(raw_client, email="fresh-signup@example.com", name="Fresh")
    user = await db.scalar(select(User).where(User.email == "fresh-signup@example.com"))
    assert user is not None
    # Then: the immediate apply failed, the rows wait in the outbox and no model is usable.
    assert user_tuples(store, user.id) == set() and await pending_rows() > 0
    listed = await raw_client.get("/api/models")
    assert listed.status_code == 200 and str(model.id) not in {m["id"] for m in listed.json()}
    # And: once OpenFGA is back the drain alone (no reconcile) makes the catalog visible.
    store.failing_objects = set()
    await drain(store, now=datetime.now(UTC) + timedelta(minutes=10))  # past the backoff
    assert ("member", f"organization:{ORG}") in user_tuples(store, user.id)
    listed = await raw_client.get("/api/models")
    assert str(model.id) in {m["id"] for m in listed.json()}
    await assert_parity(store)


async def _company_user(db: AsyncSession, fixture: OrgFixture, email: str) -> User:
    user = User(email=email, name=email.split("@")[0])
    db.add(user)
    await db.flush()
    db.add(TenantMember(tenant_id=fixture.tenant, user_id=user.id))
    await db.commit()
    return user


async def _headers(client: AsyncClient, fixture: OrgFixture, path: str) -> dict[str, str]:
    current = await client.get(f"/api/orgs/{fixture.first}{path}")
    assert current.status_code == 200, current.text
    return {**fixture.session.headers(), "If-Match": current.headers["etag"]}


async def test_member_add_suspend_reactivate_and_remove_track_expected_tuples(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    await grant_admin(db, org_fixture)
    user = await _company_user(db, org_fixture, "joiner@example.com")
    store = await reconciled_store()
    org, path = f"organization:{org_fixture.first}", f"/api/orgs/{org_fixture.first}/members"
    # Join: the member tuple is queued with the membership row.
    headers = await _headers(raw_client, org_fixture, "/members")
    added = await raw_client.post(path, json={"user_id": str(user.id)}, headers=headers)
    assert added.status_code == 201
    await assert_parity(store)
    assert ("member", org) in user_tuples(store, user.id)
    # A direct share follows the membership (admission needs an active member).
    tool = f"tool:{uuid4()}"
    db.add(
        ResourceGrant(
            org_id=org_fixture.first,
            tenant_id=org_fixture.tenant,
            resource_type="tool",
            resource_id=UUID(tool.split(":")[1]),
            relation="viewer",
            principal_type="user",
            principal_id=user.id,
        )
    )
    await db.commit()
    store = await reconciled_store()
    # Suspension and removal delete it behind a membership marker; reactivation writes it.
    for status in ("suspended", "active"):
        headers = await _headers(raw_client, org_fixture, "/members")
        changed = await raw_client.patch(
            f"{path}/{user.id}", json={"status": status}, headers=headers
        )
        assert changed.status_code == 200
        await assert_parity(store)
        held = {("member", org), ("viewer", tool)} <= user_tuples(store, user.id)
        assert held is (status == "active")
    headers = await _headers(raw_client, org_fixture, "/members")
    assert (await raw_client.delete(f"{path}/{user.id}", headers=headers)).status_code == 204
    await assert_parity(store)
    assert user_tuples(store, user.id) == {("member", f"tenant:{org_fixture.tenant}")}
    assert await markers(f"user:{user.id}") == [
        ("grant", org_fixture.first, True),  # the exit settlement revoked the share
        ("membership", org_fixture.first, True),
        ("membership", org_fixture.first, True),
    ]


async def test_role_capability_and_group_changes_track_expected_tuples(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    await grant_admin(db, org_fixture)
    user = await _company_user(db, org_fixture, "assigned@example.com")
    group = await db.scalar(select(Group).where(Group.org_id == org_fixture.first))
    assert group is not None
    store = await reconciled_store()
    base = f"/api/orgs/{org_fixture.first}"
    headers = await _headers(raw_client, org_fixture, "/members")
    joined = await raw_client.post(
        f"{base}/members", json={"user_id": str(user.id)}, headers=headers
    )
    assert joined.status_code == 201
    principal = {"type": "user", "id": str(user.id)}
    steps = [
        ("/roles", {"principal": principal, "roles": ["builder", "auditor"]}),
        ("/roles", {"principal": principal, "roles": ["auditor"]}),
        ("/capabilities", {"principal": principal, "capabilities": ["cap_connect_database"]}),
        ("/capabilities", {"principal": principal, "capabilities": []}),
        ("/roles", {"principal": {"type": "group", "id": str(group.id)}, "roles": []}),
        (
            "/capabilities",
            {
                "principal": {"type": "organization", "id": str(org_fixture.first)},
                "capabilities": ["cap_create_agent"],
            },
        ),
    ]
    for suffix, payload in steps:
        headers = await _headers(raw_client, org_fixture, suffix)
        changed = await raw_client.put(f"{base}{suffix}", json=payload, headers=headers)
        assert changed.status_code == 200, changed.text
        await assert_parity(store)
    assert ("auditor", f"organization:{org_fixture.first}") in user_tuples(store, user.id)
    # Group membership: add and remove the new member, then delete the group.
    headers = await _headers(raw_client, org_fixture, "/members")
    members = f"{base}/groups/{group.id}/members"
    added = await raw_client.post(members, json={"user_id": str(user.id)}, headers=headers)
    assert added.status_code == 201
    await assert_parity(store)
    assert ("member", f"group:{group.id}") in user_tuples(store, user.id)
    headers = await _headers(raw_client, org_fixture, "/members")
    assert (await raw_client.delete(f"{members}/{user.id}", headers=headers)).status_code == 204
    await assert_parity(store)
    headers = await _headers(raw_client, org_fixture, "/members")
    created = await raw_client.post(f"{base}/groups", json={"name": "Ops"}, headers=headers)
    assert created.status_code == 201
    await assert_parity(store)
    headers = await _headers(raw_client, org_fixture, "/members")
    deleted = await raw_client.delete(f"{base}/groups/{group.id}", headers=headers)
    assert deleted.status_code == 204
    await assert_parity(store)
    assert not any(t.object == f"group:{group.id}" for t in store.stored())
    scopes = {scope for scope, _, done in await markers(f"user:{user.id}") if done}
    assert scopes == {"role", "capability", "group_member"}
    # The group's role revocation named its member (the fixture user) as well.
    assert ("role", org_fixture.first, True) in await markers(f"user:{org_fixture.user.id}")
