"""Company membership, role and deployment policies cannot borrow operator authority."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.organization import (
    Organization,
    OrganizationMember,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.tenancy.context import resolve_tenant_context, tenant_context_from_org
from app.tenancy.deployment import (
    platform_tenant_management_enabled,
    require_platform_tenant_management,
)
from app.tenancy.permissions import (
    ADMIN_PERMISSIONS,
    AUDITOR_PERMISSIONS,
    require_tenant_permission,
    tenant_permissions,
)

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


@pytest.mark.parametrize("operator", [False, True])
async def test_missing_and_foreign_companies_are_identical_even_for_operator(
    db: AsyncSession, org_fixture: OrgFixture, operator: bool
) -> None:
    actor = replace(org_fixture.user, is_super_user=operator)
    foreign = await db.scalar(
        select(Organization.tenant_id).where(Organization.id == org_fixture.foreign)
    )
    assert foreign is not None
    errors = []
    for tenant_id in (foreign, uuid4()):
        with pytest.raises(AppError) as captured:
            await resolve_tenant_context(db, actor, tenant_id)
        errors.append((captured.value.status, captured.value.code, captured.value.message))
    assert errors == [(404, "not_found", "회사를 찾을 수 없습니다")] * 2


async def test_company_roles_are_scoped_and_operator_flag_adds_no_role(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    second = await db.scalar(
        select(Organization.tenant_id).where(Organization.id == org_fixture.second)
    )
    assert second is not None
    db.add(TenantRoleGrant(tenant_id=second, user_id=org_fixture.user.id, role="admin"))
    await db.commit()
    context = await resolve_tenant_context(db, org_fixture.user, org_fixture.tenant)
    assert context.roles == ()
    assert tenant_permissions(context) == ()
    with pytest.raises(AppError, match="회사 관리 권한"):
        require_tenant_permission(context, "can_manage_tenant")
    db.add(
        TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="auditor")
    )
    await db.commit()
    context = await resolve_tenant_context(db, org_fixture.user, org_fixture.tenant)
    assert tenant_permissions(context) == AUDITOR_PERMISSIONS
    db.add(TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin"))
    await db.commit()
    context = await resolve_tenant_context(db, org_fixture.user, org_fixture.tenant)
    assert tenant_permissions(context) == ADMIN_PERMISSIONS


@pytest.mark.parametrize(
    ("status", "code"),
    [("suspended", "tenant_suspended"), ("deletion_scheduled", "tenant_unavailable")],
)
async def test_company_status_blocks_context_without_revoking_login(
    db: AsyncSession, org_fixture: OrgFixture, status: str, code: str
) -> None:
    await db.execute(update(Tenant).where(Tenant.id == org_fixture.tenant).values(status=status))
    await db.commit()
    with pytest.raises(AppError) as captured:
        await resolve_tenant_context(db, org_fixture.user, org_fixture.tenant)
    assert (captured.value.status, captured.value.code) == (403, code)
    await db.execute(update(Tenant).where(Tenant.id == org_fixture.tenant).values(status="active"))
    await db.commit()
    assert (
        await resolve_tenant_context(db, org_fixture.user, org_fixture.tenant)
    ).tenant_id == org_fixture.tenant


async def test_derived_context_revalidates_pair_and_current_membership(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    context = OrgContext(
        org_id=org_fixture.first, tenant_id=org_fixture.tenant, user_id=org_fixture.user.id
    )
    assert (await tenant_context_from_org(db, context)).tenant_id == org_fixture.tenant
    forged = replace(context, tenant_id=uuid4())
    with pytest.raises(AppError) as captured:
        await tenant_context_from_org(db, forged)
    assert captured.value.status == 403
    await db.execute(
        delete(OrganizationMember).where(OrganizationMember.org_id == org_fixture.first)
    )
    await db.commit()
    with pytest.raises(AppError) as removed:
        await tenant_context_from_org(db, context)
    assert removed.value.status == 403


async def test_removed_company_member_cannot_reuse_role_grant(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    db.add(TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin"))
    await db.execute(
        update(TenantMember)
        .where(TenantMember.tenant_id == org_fixture.tenant)
        .values(status="removed")
    )
    await db.commit()
    with pytest.raises(AppError) as captured:
        await resolve_tenant_context(db, org_fixture.user, org_fixture.tenant)
    assert captured.value.status == 404


@pytest.mark.parametrize("operator", [False, True])
async def test_installation_mode_controls_management_exposure(
    org_fixture: OrgFixture, operator: bool
) -> None:
    actor = replace(org_fixture.user, is_super_user=operator)
    assert not platform_tenant_management_enabled(actor, "single_tenant")
    assert platform_tenant_management_enabled(actor, "multi_tenant") == operator
    with pytest.raises(AppError) as hidden:
        require_platform_tenant_management(actor, "single_tenant")
    assert hidden.value.status == 404
    if operator:
        require_platform_tenant_management(actor, "multi_tenant")
    else:
        with pytest.raises(AppError) as forbidden:
            require_platform_tenant_management(actor, "multi_tenant")
        assert forbidden.value.status == 403
