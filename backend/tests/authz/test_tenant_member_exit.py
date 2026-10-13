"""Company exit preflights every organization before membership writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import Mock
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlContext
from app.authz.org_member_exit import MemberExitPlan
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import Group, GroupMember, OrgCapabilityGrant
from app.models.skill import Skill
from app.models.user import User
from app.services.orgs import members
from app.services.tenants import member_exit
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.test_org_groups_api import grant_admin
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


@dataclass(frozen=True, slots=True)
class CompanyExitFixture:
    tenant: Tenant
    actor: CurrentUser
    target: User
    organizations: tuple[Organization, ...]


@dataclass(frozen=True, slots=True)
class InjectedLateFailure(Exception):
    organization_id: UUID

    def __str__(self) -> str:
        return f"injected failure for {self.organization_id}"


async def company_exit_fixture(db: AsyncSession) -> CompanyExitFixture:
    actor = User(email="company-admin@example.com", name="Company admin")
    target = User(email="company-member@example.com", name="Company member")
    tenant = Tenant(name="Exit company", slug="exit-company", secrets_namespace="exit-company")
    db.add_all([actor, target, tenant])
    await db.flush()
    organizations = tuple(
        Organization(tenant_id=tenant.id, name=f"Exit {index}", slug=f"exit-{index}")
        for index in range(3)
    )
    db.add_all(organizations)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=tenant.id, user_id=actor.id),
            TenantMember(tenant_id=tenant.id, user_id=target.id),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=actor.id,
                role="admin",
                granted_by=actor.id,
            ),
            TenantRoleGrant(
                tenant_id=tenant.id,
                user_id=target.id,
                role="auditor",
                granted_by=actor.id,
            ),
        ]
    )
    for organization in organizations:
        member = OrganizationMember(
            org_id=organization.id,
            tenant_id=tenant.id,
            user_id=target.id,
        )
        group = Group(
            org_id=organization.id,
            tenant_id=tenant.id,
            name=f"Group {organization.slug}",
        )
        db.add_all([member, group])
        await db.flush()
        db.add_all(
            [
                OrganizationRoleGrant(
                    org_id=organization.id,
                    tenant_id=tenant.id,
                    principal_type="user",
                    principal_id=target.id,
                    role="builder",
                    granted_by=actor.id,
                ),
                GroupMember(
                    group_id=group.id,
                    org_id=organization.id,
                    tenant_id=tenant.id,
                    user_id=target.id,
                    added_by=actor.id,
                ),
                OrgCapabilityGrant(
                    org_id=organization.id,
                    tenant_id=tenant.id,
                    principal_type="user",
                    principal_id=target.id,
                    capability="cap_create_agent",
                    granted_by=actor.id,
                ),
            ]
        )
    await db.commit()
    return CompanyExitFixture(
        tenant,
        CurrentUser(
            id=actor.id,
            email=actor.email,
            name=actor.name,
            is_super_user=actor.is_super_user,
        ),
        target,
        organizations,
    )


async def test_org_removal_preflights_before_marking_member_removed(
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an organization administrator removes an ordinary active member.
    await grant_admin(db, org_fixture)
    target = User(email="preflight-member@example.com", name="Preflight member")
    db.add(target)
    await db.flush()
    db.add(TenantMember(tenant_id=org_fixture.tenant, user_id=target.id))
    db.add(
        OrganizationMember(
            org_id=org_fixture.first,
            tenant_id=org_fixture.tenant,
            user_id=target.id,
        )
    )
    await db.commit()
    observed_statuses: list[str] = []
    original_preflight = members.preflight_member_exit

    async def observe_preflight(
        session: AsyncSession, context: OrgControlContext, user_id: UUID
    ) -> MemberExitPlan:
        member = await session.get(OrganizationMember, (context.organization.id, user_id))
        assert member is not None
        observed_statuses.append(member.status)
        return await original_preflight(session, context, user_id)

    monkeypatch.setattr(members, "preflight_member_exit", observe_preflight)
    roster = await raw_client.get(f"/api/orgs/{org_fixture.first}/members")
    headers = {**org_fixture.session.headers(), "If-Match": roster.headers["etag"]}

    # When: the removal path reaches its ownership preflight.
    removed = await raw_client.delete(
        f"/api/orgs/{org_fixture.first}/members/{target.id}", headers=headers
    )

    # Then: preflight observes the still-active member and the request can commit afterward.
    assert removed.status_code == 204
    assert observed_statuses == ["active"]


async def test_three_org_company_exit_applies_every_effect_once(db: AsyncSession) -> None:
    # Given: one member has memberships and assignments in three organizations.
    fixture = await company_exit_fixture(db)

    # When: the company-wide exit is applied and the caller commits.
    result = await exit_tenant_member(
        TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, fixture.actor)
    )
    await db.commit()

    # Then: every organization and company fact is removed with bounded audits.
    assert result.organization_ids == tuple(
        sorted(
            (organization.id for organization in fixture.organizations),
            key=lambda key: key.bytes,
        )
    )
    members = list(
        (
            await db.scalars(
                select(OrganizationMember)
                .where(OrganizationMember.user_id == fixture.target.id)
                .order_by(OrganizationMember.org_id)
            )
        ).all()
    )
    assert len(members) == 3
    assert {member.status for member in members} == {"removed"}
    tenant_member = await db.get(TenantMember, (fixture.tenant.id, fixture.target.id))
    assert tenant_member is not None and tenant_member.status == "removed"
    assert (
        await db.scalar(
            select(func.count())
            .select_from(TenantRoleGrant)
            .where(
                TenantRoleGrant.tenant_id == fixture.tenant.id,
                TenantRoleGrant.user_id == fixture.target.id,
            )
        )
        == 0
    )
    assert (
        await db.scalar(
            select(func.count())
            .select_from(OrganizationRoleGrant)
            .where(OrganizationRoleGrant.principal_id == fixture.target.id)
        )
        == 0
    )
    actions = list(
        (
            await db.scalars(
                select(AuditEvent.action).where(AuditEvent.target_id == str(fixture.target.id))
            )
        ).all()
    )
    assert actions.count("org.member_remove") == 3
    assert actions.count("tenant.member_remove") == 1


async def test_late_org_ownership_conflict_leaves_all_memberships_and_audits_unchanged(
    db: AsyncSession,
) -> None:
    # Given: the member owns a resource only in the last UUID-sorted organization.
    fixture = await company_exit_fixture(db)
    last = sorted(fixture.organizations, key=lambda organization: organization.id.bytes)[-1]
    db.add(
        Skill(
            user_id=fixture.target.id,
            name="Transfer me",
            slug="transfer-me",
            org_id=last.id,
            tenant_id=fixture.tenant.id,
        )
    )
    await db.commit()

    # When: company exit preflights every organization.
    with pytest.raises(AppError) as captured:
        await exit_tenant_member(
            TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, fixture.actor)
        )

    # Then: the real ownership conflict is typed and no earlier organization changed.
    assert captured.value.status == 409
    assert captured.value.code == "resource_transfer_required"
    members = list(
        (
            await db.scalars(
                select(OrganizationMember).where(OrganizationMember.user_id == fixture.target.id)
            )
        ).all()
    )
    assert {member.status for member in members} == {"active"}
    assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0


async def test_late_apply_failure_rolls_back_every_organization(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid three-organization exit whose second apply fails.
    fixture = await company_exit_fixture(db)
    original_apply = member_exit.apply_member_exit
    calls = 0

    async def fail_second_apply(session: AsyncSession, plan: MemberExitPlan) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise InjectedLateFailure(plan.context.organization.id)
        await original_apply(session, plan)

    monkeypatch.setattr(member_exit, "apply_member_exit", fail_second_apply)

    # When: the late failure is raised after one in-memory apply.
    with pytest.raises(InjectedLateFailure):
        await exit_tenant_member(
            TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, fixture.actor)
        )

    # Then: the savepoint restores every membership and emits no audit.
    members = list(
        (
            await db.scalars(
                select(OrganizationMember).where(OrganizationMember.user_id == fixture.target.id)
            )
        ).all()
    )
    assert {member.status for member in members} == {"active"}
    assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize(
    ("actor_state", "expected"),
    [
        ("foreign", (404, "not_found")),
        ("inactive", (404, "not_found")),
        ("non_admin", (403, "permission_required")),
    ],
)
async def test_company_exit_checks_active_membership_before_company_authority(
    db: AsyncSession, actor_state: str, expected: tuple[int, str]
) -> None:
    # Given: a foreign, inactive, or active non-admin actor targets an active company member.
    fixture = await company_exit_fixture(db)
    actor = fixture.actor
    if actor_state == "foreign":
        outsider = User(email="company-outsider@example.com", name="Company outsider")
        db.add(outsider)
        await db.commit()
        actor = CurrentUser(
            id=outsider.id,
            email=outsider.email,
            name=outsider.name,
            is_super_user=False,
        )
    elif actor_state == "inactive":
        await db.execute(
            update(TenantMember)
            .where(
                TenantMember.tenant_id == fixture.tenant.id,
                TenantMember.user_id == fixture.actor.id,
            )
            .values(status="suspended")
        )
        await db.commit()
    else:
        await db.execute(
            update(TenantRoleGrant)
            .where(
                TenantRoleGrant.tenant_id == fixture.tenant.id,
                TenantRoleGrant.user_id == fixture.actor.id,
            )
            .values(role="auditor")
            .execution_options(synchronize_session=False)
        )
        await db.commit()

    # When: the actor requests a company-wide exit.
    with pytest.raises(AppError) as captured:
        await exit_tenant_member(
            TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, actor)
        )

    # Then: membership hides the company and only an active member reaches the role denial.
    assert (captured.value.status, captured.value.code) == expected


async def test_denied_company_exit_does_not_autoflush_unrelated_pending_tenant(
    db: AsyncSession,
) -> None:
    # Given: an active non-admin attempts exit while an unrelated Tenant is pending.
    fixture = await company_exit_fixture(db)
    pending = Tenant(
        name="Pending unrelated company",
        slug="pending-unrelated-company",
        secrets_namespace="pending/unrelated-company",
    )
    db.add(pending)
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    actor = CurrentUser(
        id=fixture.target.id,
        email=fixture.target.email,
        name=fixture.target.name,
        is_super_user=False,
    )

    # When: the fresh authority check denies the request.
    try:
        with pytest.raises(AppError) as captured:
            await exit_tenant_member(
                TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, actor)
            )
    finally:
        event.remove(db.sync_session, "before_flush", flush)

    # Then: denial is typed and no pending write crossed the authority boundary.
    with db.no_autoflush:
        persisted = await db.scalar(select(Tenant.id).where(Tenant.slug == pending.slug))
    assert db.autoflush is True
    assert (captured.value.status, captured.value.code) == (403, "permission_required")
    assert flush.call_count == 0
    assert pending in db.new
    assert persisted is None
    assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0


async def test_company_exit_reloads_preloaded_actor_role_after_external_revocation(
    db: AsyncSession,
) -> None:
    # Given: this session cached admin, then another committed session revoked it.
    fixture = await company_exit_fixture(db)
    cached = await db.scalar(
        select(TenantRoleGrant).where(
            TenantRoleGrant.tenant_id == fixture.tenant.id,
            TenantRoleGrant.user_id == fixture.actor.id,
        )
    )
    assert cached is not None and cached.role == "admin"
    await db.commit()
    async with TestSession() as other:
        await other.execute(
            update(TenantRoleGrant)
            .where(TenantRoleGrant.id == cached.id)
            .values(role="auditor")
            .execution_options(synchronize_session=False)
        )
        await other.commit()

    # When: the cached session attempts a company-wide exit.
    with pytest.raises(AppError) as captured:
        await exit_tenant_member(
            TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, fixture.actor)
        )

    # Then: fresh locked authority denies the revoked administrator without side effects.
    assert (captured.value.status, captured.value.code) == (403, "permission_required")
    assert cached.role == "auditor"
    assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0


async def test_company_exit_rejects_suspended_company_under_the_tenant_lock(
    db: AsyncSession,
) -> None:
    # Given: the actor and target remain active members of a suspended company.
    fixture = await company_exit_fixture(db)
    await db.execute(
        update(Tenant).where(Tenant.id == fixture.tenant.id).values(status="suspended")
    )
    await db.commit()

    # When: the administrator requests a company-wide exit.
    with pytest.raises(AppError) as captured:
        await exit_tenant_member(
            TenantMemberExitRequest(db, fixture.tenant.id, fixture.target.id, fixture.actor)
        )

    # Then: suspended company state blocks removal before any audit or membership write.
    assert (captured.value.status, captured.value.code) == (403, "tenant_suspended")
    tenant_member = await db.get(TenantMember, (fixture.tenant.id, fixture.target.id))
    assert tenant_member is not None and tenant_member.status == "active"
    assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
