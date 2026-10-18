"""Company-level membership events queue their tuples (7.2.1; company exit removes all)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from app.authz.fake import FakeAuthz
from app.authz.principal import Principal
from app.authz.revocation_overlay import DbRevocationOverlay
from app.authz.service import AuthzService
from app.models.authz import ResourceGrant
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
)
from app.models.organization_group import Group, GroupMember
from tests.authz.membership_sync_testkit import (
    assert_parity,
    delete_marker_orgs,
    markers,
    reconciled_store,
    user_tuples,
)
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.tenant_members_fixture import TenantMembersFixture

pytest_plugins = ["tests.authz.tenant_members_fixture"]


async def test_company_member_roles_suspension_and_exit_track_expected_tuples(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    tenant = fixture.tenant
    store = await reconciled_store()
    session = await fixture.login(fixture.admin)
    base = f"/api/tenants/{tenant.id}"
    company = f"tenant:{tenant.id}"
    # Join the company, then gain and swap a company role.
    added = await fixture.client.post(
        f"{base}/members",
        json={"user_id": str(fixture.target.id)},
        headers=session.headers(await fixture.version()),
    )
    assert added.status_code == 201, added.text
    await assert_parity(store)
    assert user_tuples(store, fixture.target.id) == {("member", company)}
    for roles in (["admin", "auditor"], ["auditor"]):
        changed = await fixture.client.put(
            f"{base}/roles/{fixture.target.id}",
            json={"roles": roles},
            headers=session.headers(await fixture.version()),
        )
        assert changed.status_code == 200, changed.text
        await assert_parity(store)
    assert ("auditor", company) in user_tuples(store, fixture.target.id)
    # Suspension drops the company tuples (roles need an active membership), reactivation
    # brings them back.
    for status in ("suspended", "active"):
        changed = await fixture.client.patch(
            f"{base}/members/{fixture.target.id}",
            json={"status": status},
            headers=session.headers(await fixture.version()),
        )
        assert changed.status_code == 200, changed.text
        await assert_parity(store)
        assert bool(user_tuples(store, fixture.target.id)) is (status == "active")


async def test_company_exit_deletes_every_organization_tuple_behind_markers(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    db, tenant, user = fixture.db, fixture.tenant, fixture.member
    orgs = [Organization(tenant_id=tenant.id, name=n, slug=n) for n in ("exit-a", "exit-b")]
    db.add_all(orgs)
    await db.flush()
    group = Group(org_id=orgs[0].id, tenant_id=tenant.id, name="Exiters")
    db.add(group)
    for org in orgs:
        db.add(OrganizationMember(org_id=org.id, tenant_id=tenant.id, user_id=user.id))
    await db.flush()
    db.add(GroupMember(group_id=group.id, org_id=orgs[0].id, tenant_id=tenant.id, user_id=user.id))
    db.add(
        OrganizationRoleGrant(
            org_id=orgs[1].id,
            tenant_id=tenant.id,
            role="builder",
            principal_type="user",
            principal_id=user.id,
        )
    )
    shares = {org.id: uuid4() for org in orgs}  # a direct share in each organization
    db.add_all(
        ResourceGrant(
            org_id=org,
            tenant_id=tenant.id,
            resource_type="tool",
            resource_id=tool,
            relation="viewer",
            principal_type="user",
            principal_id=user.id,
        )
        for org, tool in shares.items()
    )
    await db.commit()
    store = await reconciled_store()
    assert len(user_tuples(store, user.id)) == 7  # company, two orgs, builder, group, shares
    session = await fixture.login(fixture.admin)
    removed = await fixture.client.delete(
        f"/api/tenants/{tenant.id}/members/{user.id}",
        headers=session.headers(await fixture.version()),
    )
    assert removed.status_code == 204, removed.text
    await assert_parity(store)
    assert user_tuples(store, user.id) == set()
    assert await markers(f"user:{user.id}") == sorted(("membership", org.id, True) for org in orgs)
    # Each share's delete finalizes with the marker of its own organization.
    for org, tool in shares.items():
        assert await delete_marker_orgs(f"tool:{tool}") == {org}


async def test_company_admin_demotion_is_denied_at_once_by_markers_in_every_org(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    db, tenant, user = fixture.db, fixture.tenant, fixture.peer_admin
    orgs = [Organization(tenant_id=tenant.id, name=n, slug=n) for n in ("gov-a", "gov-b")]
    db.add_all(orgs)
    await db.flush()
    db.add(OrganizationMember(org_id=orgs[0].id, tenant_id=tenant.id, user_id=user.id))
    await db.commit()
    store = await reconciled_store()
    # OpenFGA still answers from the stale ``admin from tenant`` tuple: only markers deny.
    stale = frozenset(
        (f"user:{user.id}", "can_manage_members", f"organization:{o.id}") for o in orgs
    )
    authz = AuthzService(
        FakeAuthz(stale),
        mode="enforce",
        overlay=DbRevocationOverlay(TestSession),  # process hint: noted at record time
    )

    async def governs(org: Organization) -> bool:
        principal = Principal(kind="user", id=user.id, org_id=org.id)
        return await authz.check(principal, "can_manage_members", f"organization:{org.id}")

    assert all([await governs(org) for org in orgs])
    session = await fixture.login(fixture.admin)
    demoted = await fixture.client.put(
        f"/api/tenants/{tenant.id}/roles/{user.id}",
        json={"roles": []},
        headers=session.headers(await fixture.version()),
    )
    assert demoted.status_code == 200, demoted.text
    # Before any drain (the fixture app has no writer): every organization refuses.
    assert not any([await governs(org) for org in orgs])
    assert await markers(f"user:{user.id}") == sorted(("role", o.id, False) for o in orgs)
    await assert_parity(store)
    assert await markers(f"user:{user.id}") == sorted(("role", o.id, True) for o in orgs)
    assert ("admin", f"tenant:{tenant.id}") not in user_tuples(store, user.id)
    # Company suspension marks only the organizations the member belongs to.
    suspended = await fixture.client.patch(
        f"/api/tenants/{tenant.id}/members/{user.id}",
        json={"status": "suspended"},
        headers=session.headers(await fixture.version()),
    )
    assert suspended.status_code == 200, suspended.text
    await assert_parity(store)
    assert ("membership", orgs[0].id, True) in await markers(f"user:{user.id}")
    assert ("membership", orgs[1].id, True) not in await markers(f"user:{user.id}")


async def test_company_admin_created_organization_projects_tenant_and_administrator(
    tenant_members_fixture: TenantMembersFixture,
) -> None:
    fixture = tenant_members_fixture
    store = await reconciled_store()
    session = await fixture.login(fixture.admin)
    created = await fixture.client.post(
        f"/api/tenants/{fixture.tenant.id}/organizations",
        json={"name": "Created Org", "administrator": {"user_id": str(fixture.member.id)}},
        headers=session.headers(await fixture.version()),
    )
    assert created.status_code == 201, created.text
    org_id = created.json()["organization"]["id"]
    await assert_parity(store)
    org = f"organization:{org_id}"
    assert {("member", org), ("admin", org)} <= user_tuples(store, fixture.member.id)
    tenant_tuple = (f"tenant:{fixture.tenant.id}", "tenant", org)
    assert tenant_tuple in {(t.user, t.relation, t.object) for t in store.stored()}
