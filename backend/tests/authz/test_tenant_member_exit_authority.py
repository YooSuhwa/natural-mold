"""Company-exit authority and denied-write scenarios."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.models.user import User
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.tenant_member_exit_fixture import company_exit_fixture
from tests.conftest import TestSession

pytest_plugins = ["tests.authz.org_context_fixture"]


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
