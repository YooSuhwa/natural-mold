"""Service-level transaction and fresh-authority tests for company overview."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant, TenantRoleGrant
from app.schemas.tenant_overview import TenantOverviewPatch
from app.services import audit_service
from app.services.tenants.overview import TenantOverviewMutation, update_tenant_overview
from app.services.tenants.versioning import etag
from app.tenancy.admin_context import TenantAdminTarget, load_tenant_admin_context

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


def _request(db: AsyncSession, expected_version: str) -> TenantOverviewMutation:
    return TenantOverviewMutation(
        db=db,
        request=Request({"type": "http", "method": "PATCH", "path": "/direct", "headers": []}),
        expected_version=expected_version,
    )


async def test_update_rechecks_fresh_locked_company_authority(
    db: AsyncSession, org_fixture: OrgFixture
) -> None:
    grant = TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin")
    db.add(grant)
    await db.commit()
    context = await load_tenant_admin_context(
        db,
        org_fixture.user,
        TenantAdminTarget(org_fixture.tenant, permission="can_manage_tenant"),
    )
    expected_version = etag(context.tenant.updated_at)
    await db.execute(delete(TenantRoleGrant).where(TenantRoleGrant.id == grant.id))
    await db.commit()

    with pytest.raises(AppError) as captured:
        await update_tenant_overview(
            _request(db, expected_version), context, TenantOverviewPatch(name="Rejected")
        )

    assert captured.value.code == "permission_required"
    tenant = await db.get(Tenant, org_fixture.tenant, populate_existing=True)
    assert tenant is not None and tenant.name == "A"


async def test_commit_failure_rolls_back_name_version_and_audit(
    db: AsyncSession, org_fixture: OrgFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.add(TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin"))
    await db.commit()
    context = await load_tenant_admin_context(
        db,
        org_fixture.user,
        TenantAdminTarget(org_fixture.tenant, permission="can_manage_tenant"),
    )
    original_name = context.tenant.name
    original_updated_at = context.tenant.updated_at

    async def fail_commit() -> None:
        raise SQLAlchemyError("forced commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(SQLAlchemyError, match="forced commit failure"):
        await update_tenant_overview(
            _request(db, etag(original_updated_at)),
            context,
            TenantOverviewPatch(name="Rolled Back"),
        )

    tenant = await db.get(Tenant, org_fixture.tenant, populate_existing=True)
    assert tenant is not None
    assert (tenant.name, tenant.updated_at) == (original_name, original_updated_at)
    assert (
        await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "tenant.update")
        )
        == 0
    )


async def test_audit_failure_rolls_back_name_and_version(
    db: AsyncSession, org_fixture: OrgFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.add(TenantRoleGrant(tenant_id=org_fixture.tenant, user_id=org_fixture.user.id, role="admin"))
    await db.commit()
    context = await load_tenant_admin_context(
        db,
        org_fixture.user,
        TenantAdminTarget(org_fixture.tenant, permission="can_manage_tenant"),
    )
    original_name = context.tenant.name
    original_updated_at = context.tenant.updated_at
    monkeypatch.setattr(
        audit_service,
        "record_event",
        AsyncMock(side_effect=SQLAlchemyError("forced audit failure")),
    )

    with pytest.raises(SQLAlchemyError, match="forced audit failure"):
        await update_tenant_overview(
            _request(db, etag(original_updated_at)),
            context,
            TenantOverviewPatch(name="Rolled Back"),
        )

    tenant = await db.get(Tenant, org_fixture.tenant, populate_existing=True)
    assert tenant is not None
    assert (tenant.name, tenant.updated_at) == (original_name, original_updated_at)
    assert (
        await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "tenant.update")
        )
        == 0
    )
