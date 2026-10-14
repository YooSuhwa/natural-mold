"""Foreign-key-enforced HTTP regression for atomic company creation."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, text

from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.tenant_invitation import TenantInvitation
from tests.authz.platform_tenant_creation_fixture import creation_payload
from tests.authz.platform_tenant_creation_fk_fixture import ForeignKeyTenantCreationCase

pytest_plugins = ["tests.authz.platform_tenant_creation_fk_fixture"]


async def test_create_commits_complete_aggregate_with_foreign_keys_enforced(
    fk_tenant_creation_case: ForeignKeyTenantCreationCase,
) -> None:
    case = fk_tenant_creation_case
    response = await case.client.post(
        "/api/admin/tenants",
        json=creation_payload("task12-fk-enforced"),
        headers=case.headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert "secrets_namespace" not in response.text and "token_hash" not in response.text
    tenant_id = UUID(body["tenant"]["id"])
    organization_id = UUID(body["organization"]["id"])
    async with case.observer() as db:
        assert await db.scalar(text("PRAGMA foreign_keys")) == 1
        counts = (
            await db.scalar(select(func.count()).select_from(Tenant).where(Tenant.id == tenant_id)),
            await db.scalar(
                select(func.count())
                .select_from(Organization)
                .where(Organization.id == organization_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(TenantInvitation)
                .where(TenantInvitation.tenant_id == tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "tenant.create", AuditEvent.target_id == str(tenant_id))
            ),
            await db.scalar(
                select(func.count())
                .select_from(TenantMember)
                .where(TenantMember.tenant_id == tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(TenantRoleGrant)
                .where(TenantRoleGrant.tenant_id == tenant_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(OrganizationMember.org_id == organization_id)
            ),
            await db.scalar(
                select(func.count())
                .select_from(OrganizationRoleGrant)
                .where(OrganizationRoleGrant.org_id == organization_id)
            ),
        )
    assert tuple(int(value or 0) for value in counts) == (1, 1, 1, 1, 0, 0, 0, 0)
