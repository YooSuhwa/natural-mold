from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.organization import (
    Organization,
    OrganizationMember,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.tenancy.admin_context import TenantAdminTarget, load_tenant_admin_context

pytest_plugins = ["tests.authz.org_context_fixture"]


@pytest.mark.parametrize(
    ("role", "expected_access"), [(None, "member"), ("auditor", "auditor"), ("admin", "admin")]
)
async def test_active_company_roles_load_fresh_path_bound_access(
    db: AsyncSession, org_fixture, role: str | None, expected_access: str
) -> None:
    if role is not None:
        db.add(
            TenantRoleGrant(
                tenant_id=org_fixture.tenant,
                user_id=org_fixture.user.id,
                role=role,
            )
        )
        await db.commit()

    context = await load_tenant_admin_context(
        db, org_fixture.user, TenantAdminTarget(org_fixture.tenant)
    )

    assert context.tenant.id == org_fixture.tenant
    assert context.access == expected_access


async def test_auditor_cannot_mutate_but_admin_uses_fresh_authority(
    db: AsyncSession, org_fixture
) -> None:
    grant = TenantRoleGrant(
        tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="auditor"
    )
    db.add(grant)
    await db.commit()
    target = TenantAdminTarget(org_fixture.tenant, permission="can_manage_tenant")

    with pytest.raises(AppError) as denied:
        await load_tenant_admin_context(db, org_fixture.user, target)
    assert (denied.value.status, denied.value.code) == (403, "permission_required")

    grant.role = "admin"
    await db.commit()
    assert (await load_tenant_admin_context(db, org_fixture.user, target)).access == "admin"

    await db.execute(delete(TenantRoleGrant).where(TenantRoleGrant.id == grant.id))
    await db.commit()
    with pytest.raises(AppError) as stale:
        await load_tenant_admin_context(db, org_fixture.user, target)
    assert (stale.value.status, stale.value.code) == (403, "permission_required")


async def test_operator_flag_never_adds_implicit_company_authority(
    db: AsyncSession, org_fixture
) -> None:
    operator = replace(org_fixture.user, is_super_user=True)
    explicit = await load_tenant_admin_context(db, operator, TenantAdminTarget(org_fixture.tenant))
    assert explicit.access == "member"
    db.add(
        TenantRoleGrant(
            tenant_id=org_fixture.tenant,
            user_id=org_fixture.user.id,
            role="admin",
        )
    )
    await db.commit()
    assert (
        await load_tenant_admin_context(db, operator, TenantAdminTarget(org_fixture.tenant))
    ).access == "admin"

    foreign_tenant = await db.scalar(
        select(Organization.tenant_id).where(Organization.id == org_fixture.foreign)
    )
    assert foreign_tenant is not None
    errors = []
    for tenant_id in (foreign_tenant, uuid4()):
        with pytest.raises(AppError) as captured:
            await load_tenant_admin_context(db, operator, TenantAdminTarget(tenant_id))
        errors.append((captured.value.status, captured.value.code, captured.value.message))
    assert errors == [(404, "not_found", "회사를 찾을 수 없습니다")] * 2


async def test_company_overview_does_not_require_organization_membership(
    db: AsyncSession, org_fixture
) -> None:
    await db.execute(
        delete(OrganizationMember).where(OrganizationMember.user_id == org_fixture.user.id)
    )
    await db.commit()

    context = await load_tenant_admin_context(
        db, org_fixture.user, TenantAdminTarget(org_fixture.tenant)
    )
    assert context.access == "member"


async def test_inactive_company_member_is_enumeration_safe(db: AsyncSession, org_fixture) -> None:
    await db.execute(
        update(TenantMember)
        .where(TenantMember.tenant_id == org_fixture.tenant)
        .values(status="suspended")
    )
    await db.commit()

    with pytest.raises(AppError) as captured:
        await load_tenant_admin_context(db, org_fixture.user, TenantAdminTarget(org_fixture.tenant))
    assert (captured.value.status, captured.value.code) == (404, "not_found")


@pytest.mark.parametrize(
    ("status", "code"),
    [("suspended", "tenant_suspended"), ("deletion_scheduled", "tenant_unavailable")],
)
async def test_nonplatform_company_status_denial_is_preserved(
    db: AsyncSession, org_fixture, status: str, code: str
) -> None:
    await db.execute(update(Tenant).where(Tenant.id == org_fixture.tenant).values(status=status))
    await db.commit()

    with pytest.raises(AppError) as captured:
        await load_tenant_admin_context(db, org_fixture.user, TenantAdminTarget(org_fixture.tenant))
    assert (captured.value.status, captured.value.code) == (403, code)
