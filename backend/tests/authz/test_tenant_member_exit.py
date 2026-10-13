"""Company exit preflights every organization before membership writes."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlContext
from app.authz.org_member_exit import MemberExitPlan
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import (
    OrganizationMember,
    OrganizationRoleGrant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.skill import Skill
from app.models.user import User
from app.services.orgs import members
from app.services.tenants import member_exit
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.tenant_member_exit_fixture import (
    InjectedLateFailure,
    company_exit_fixture,
)
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


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
