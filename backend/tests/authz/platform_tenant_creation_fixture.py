"""Actual auth/CSRF fixture for operator company creation."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token, create_csrf_token
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
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
from app.models.user import User
from app.services import audit_service
from tests.conftest import TestSession

ACTOR_ID = UUID("12121212-1212-4121-8121-121212121212")


@dataclass(frozen=True, slots=True)
class PlatformTenantCreationCase:
    client: AsyncClient
    db: AsyncSession
    actor_id: UUID
    csrf: str

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}


@dataclass(frozen=True, slots=True)
class TenantAggregateCounts:
    tenants: int
    organizations: int
    invitations: int
    audits: int
    tenant_members: int
    tenant_roles: int
    organization_members: int
    organization_roles: int


def creation_payload(
    slug: str = "task12-company",
    *,
    name: str = "Task 12 Company",
    organization_name: str = "Task 12 Organization",
) -> dict[str, str | int | bool]:
    return {
        "name": name,
        "slug": slug,
        "first_admin_email": "first-admin@example.com",
        "first_organization_name": organization_name,
        "member_limit": 5,
        "monthly_spend_limit": "100.00",
        "concurrent_run_limit": 2,
        "allow_system_models": False,
    }


async def aggregate_counts(tenant_id: UUID) -> TenantAggregateCounts:
    async with TestSession() as db:
        organization_ids = select(Organization.id).where(Organization.tenant_id == tenant_id)
        return TenantAggregateCounts(
            tenants=int(
                await db.scalar(
                    select(func.count()).select_from(Tenant).where(Tenant.id == tenant_id)
                )
                or 0
            ),
            organizations=int(
                await db.scalar(
                    select(func.count())
                    .select_from(Organization)
                    .where(Organization.tenant_id == tenant_id)
                )
                or 0
            ),
            invitations=int(
                await db.scalar(
                    select(func.count())
                    .select_from(TenantInvitation)
                    .where(TenantInvitation.tenant_id == tenant_id)
                )
                or 0
            ),
            audits=int(
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.action == "tenant.create", AuditEvent.target_id == str(tenant_id)
                    )
                )
                or 0
            ),
            tenant_members=int(
                await db.scalar(
                    select(func.count())
                    .select_from(TenantMember)
                    .where(TenantMember.tenant_id == tenant_id)
                )
                or 0
            ),
            tenant_roles=int(
                await db.scalar(
                    select(func.count())
                    .select_from(TenantRoleGrant)
                    .where(TenantRoleGrant.tenant_id == tenant_id)
                )
                or 0
            ),
            organization_members=int(
                await db.scalar(
                    select(func.count())
                    .select_from(OrganizationMember)
                    .where(OrganizationMember.org_id.in_(organization_ids))
                )
                or 0
            ),
            organization_roles=int(
                await db.scalar(
                    select(func.count())
                    .select_from(OrganizationRoleGrant)
                    .where(OrganizationRoleGrant.org_id.in_(organization_ids))
                )
                or 0
            ),
        )


@pytest.fixture
async def platform_tenant_creation_case(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[PlatformTenantCreationCase, None]:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    actor = User(
        id=ACTOR_ID,
        email="task12-operator@example.com",
        name="Task 12 Operator",
        hashed_password="unused",
        is_active=True,
        is_super_user=True,
    )
    db.add(actor)
    await db.commit()

    app: FastAPI = create_app()

    async def current_session() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db] = current_session
    csrf = create_csrf_token(actor.id)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(
            settings.cookie_name_access, create_access_token(actor.id, is_super_user=True)
        )
        client.cookies.set(settings.cookie_name_csrf, csrf)
        yield PlatformTenantCreationCase(client, db, actor.id, csrf)

    assert ACTIVE_ORG_COOKIE not in client.cookies
