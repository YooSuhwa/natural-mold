"""PostgreSQL late-failure rollback for atomic company creation."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.organization import Organization, Tenant
from app.models.tenant_invitation import TenantInvitation
from app.services.platform import tenant_creation
from tests.authz.platform_tenant_creation_native_fixture import (
    NativeCreationDatabase,
    NativeCreationFacts,
    creation_facts,
    native_owned_creation,
)

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.platform_tenant_creation_native_fixture"]


def _payload(stage: str) -> dict[str, str | int | bool]:
    return {
        "name": f"Task 12 rollback {stage}",
        "slug": f"task12-rollback-{stage}",
        "first_admin_email": "task12-rollback-admin@example.com",
        "first_organization_name": f"Task 12 rollback {stage}",
        "member_limit": 3,
        "monthly_spend_limit": "25.00",
        "concurrent_run_limit": 1,
        "allow_system_models": False,
    }


@pytest.mark.parametrize(
    ("stage", "failure_flush", "tenant_id", "organization_id", "invitation_id"),
    [
        pytest.param(
            "organization",
            2,
            UUID("12100000-0000-4000-8000-000000000011"),
            UUID("12100000-0000-4000-8000-000000000012"),
            UUID("12100000-0000-4000-8000-000000000013"),
            id="organization-stage",
        ),
        pytest.param(
            "invitation",
            3,
            UUID("12100000-0000-4000-8000-000000000021"),
            UUID("12100000-0000-4000-8000-000000000022"),
            UUID("12100000-0000-4000-8000-000000000023"),
            id="invitation-stage",
        ),
        pytest.param(
            "audit",
            4,
            UUID("12100000-0000-4000-8000-000000000031"),
            UUID("12100000-0000-4000-8000-000000000032"),
            UUID("12100000-0000-4000-8000-000000000033"),
            id="audit-stage",
        ),
    ],
)
async def test_reached_late_stage_failure_has_zero_committed_residue(
    native_creation_database: NativeCreationDatabase,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    failure_flush: int,
    tenant_id: UUID,
    organization_id: UUID,
    invitation_id: UUID,
) -> None:
    database = native_creation_database
    actor_id = UUID("12100000-0000-4000-8000-000000000001")
    foreign_tenant_id = UUID(int=tenant_id.int + 1000)
    foreign_organization_id = UUID(int=tenant_id.int + 1001)
    ids: Iterator[UUID] = iter((tenant_id, organization_id, invitation_id))
    reached = False
    flushes = 0
    original_flush = AsyncSession.flush

    def owned_uuid4() -> UUID:
        return next(ids)

    async def fail_after_reached_flush(db: AsyncSession) -> None:
        nonlocal flushes, reached
        flushes += 1
        await original_flush(db)
        if flushes != failure_flush:
            return
        persisted_tenant = await db.get(Tenant, tenant_id)
        persisted_organization = await db.get(Organization, organization_id)
        assert persisted_tenant is not None and persisted_organization is not None
        if failure_flush >= 3:
            assert await db.get(TenantInvitation, invitation_id) is not None
        if failure_flush == 4:
            assert (
                await db.scalar(
                    select(AuditEvent.id).where(
                        AuditEvent.action == "tenant.create",
                        AuditEvent.target_id == str(tenant_id),
                    )
                )
                is not None
            )
        reached = True
        raise SQLAlchemyError(f"injected {stage} stage failure")

    try:
        async with database.observer() as observer:
            observer.add(
                Tenant(
                    id=foreign_tenant_id,
                    name=f"Task 12 foreign {stage}",
                    slug=f"task12-foreign-{stage}",
                    secrets_namespace=f"tenants/{foreign_tenant_id}",
                )
            )
            await observer.flush()
            observer.add(
                Organization(
                    id=foreign_organization_id,
                    tenant_id=foreign_tenant_id,
                    name=f"Task 12 foreign default {stage}",
                    slug="default",
                    is_default=True,
                    settings={"default_member_role": "member"},
                )
            )
            await observer.commit()
        monkeypatch.setattr(tenant_creation, "uuid4", owned_uuid4)
        monkeypatch.setattr(AsyncSession, "flush", fail_after_reached_flush)
        async with (
            native_owned_creation(database, actor_id=actor_id, tenant_ids=(tenant_id,)),
            database.client(database.primary, actor_id) as client,
        ):
            response = await client.post("/api/admin/tenants", json=_payload(stage))
            assert reached
            assert response.status_code == 500
            assert "token_hash" not in response.text
            assert "tenants/" not in response.text
            assert await creation_facts(database.observer, tenant_id) == NativeCreationFacts(
                0, 0, 0, 0, 0, 0, 0, 0
            )
        async with database.observer() as observer:
            assert await observer.get(Tenant, foreign_tenant_id) is not None
            assert await observer.get(Organization, foreign_organization_id) is not None
    finally:
        async with database.observer() as observer:
            await observer.execute(
                delete(Organization).where(Organization.id == foreign_organization_id)
            )
            await observer.execute(delete(Tenant).where(Tenant.id == foreign_tenant_id))
            await observer.commit()
