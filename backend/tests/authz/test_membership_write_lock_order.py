"""Membership writers acquire company scope before organization scope."""

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.authz.invitation_queries import locked_invitation_scope
from app.authz.registration_queries import DEFAULT_COMPANY_ID, locked_default_organization
from app.config import settings
from app.models.organization import Organization, Tenant
from app.models.organization_group import OrgInvitation
from app.schemas.auth import RegisterRequest
from app.services.registration_policy import prepare_registration
from tests.authz.membership_write_lock_fixture import (
    InvitationScalarResult,
    RegistrationScalarResult,
)


async def test_invitation_acceptance_locks_tenant_before_organization(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a token hint identifying one existing company and organization.
    tenant = Tenant(name="Lock company", slug="lock-company", secrets_namespace="lock-company")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="Lock org", slug="lock-org")
    db.add(organization)
    await db.flush()
    token = "task-five-lock-order-token"
    invitation = OrgInvitation(
        org_id=organization.id,
        tenant_id=tenant.id,
        email="lock-recipient@example.com",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db.add(invitation)
    await db.commit()
    locked_tables: list[str] = []
    original_scalar: Callable[[Executable], Awaitable[InvitationScalarResult]] = db.scalar

    async def record_locked_table(
        statement: Executable,
    ) -> InvitationScalarResult:
        sql = str(statement)
        if "FOR UPDATE" in sql:
            for table in ("tenants", "organizations", "org_invitations"):
                if f"FROM {table}" in sql:
                    locked_tables.append(table)
                    break
        return await original_scalar(statement)

    monkeypatch.setattr(db, "scalar", record_locked_table)

    # When: acceptance resolves and locks the hinted scope.
    await locked_invitation_scope(db, invitation.token_hash)

    # Then: the shared parent is locked before its organization and invitation child.
    assert locked_tables == ["tenants", "organizations", "org_invitations"]


async def test_default_registration_locks_tenant_before_organization(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the fixed bootstrap company and its default organization.
    tenant = Tenant(
        id=DEFAULT_COMPANY_ID,
        name="Default lock company",
        slug="default-lock-company",
        secrets_namespace="default-lock-company",
    )
    db.add(tenant)
    await db.flush()
    organization = Organization(
        tenant_id=tenant.id,
        name="Default lock org",
        slug="default-lock-org",
        is_default=True,
    )
    db.add(organization)
    await db.commit()
    locked_tables: list[str] = []
    original_scalar: Callable[[Executable], Awaitable[RegistrationScalarResult]] = db.scalar

    async def record_locked_table(statement: Executable) -> RegistrationScalarResult:
        result = await original_scalar(statement)
        sql = str(statement)
        if "FOR UPDATE" in sql:
            for table in ("tenants", "organizations"):
                if f"FROM {table}" in sql:
                    locked_tables.append(table)
                    break
        return result

    monkeypatch.setattr(db, "scalar", record_locked_table)

    # When: default registration resolves its company and organization.
    await locked_default_organization(db)

    # Then: the fixed company is the parent lock.
    assert locked_tables == ["tenants", "organizations"]


async def test_cross_company_invited_signup_locks_all_tenants_before_any_organization(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: first-account bootstrap is possible and an invitation targets another company.
    default = Tenant(
        id=DEFAULT_COMPANY_ID,
        name="Default company",
        slug="mixed-default-company",
        secrets_namespace="mixed-default-company",
    )
    invited = Tenant(
        name="Invited company",
        slug="mixed-invited-company",
        secrets_namespace="mixed-invited-company",
    )
    db.add_all([default, invited])
    await db.flush()
    default_org = Organization(
        tenant_id=default.id,
        name="Default org",
        slug="mixed-default-org",
        is_default=True,
    )
    invited_org = Organization(
        tenant_id=invited.id,
        name="Invited org",
        slug="mixed-invited-org",
    )
    db.add_all([default_org, invited_org])
    await db.flush()
    token = "cross-company-registration-lock"
    db.add(
        OrgInvitation(
            org_id=invited_org.id,
            tenant_id=invited.id,
            email="cross-company@example.com",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await db.commit()
    locked_tables: list[str] = []
    original_scalar: Callable[[Executable], Awaitable[RegistrationScalarResult]] = db.scalar

    async def record_locked_table(statement: Executable) -> RegistrationScalarResult:
        result = await original_scalar(statement)
        sql = str(statement)
        if "FOR UPDATE" in sql:
            for table in ("tenants", "organizations", "org_invitations"):
                if f"FROM {table}" in sql:
                    locked_tables.append(table)
                    break
        return result

    monkeypatch.setattr(db, "scalar", record_locked_table)
    monkeypatch.setattr(settings, "allow_first_user_as_admin", True)
    monkeypatch.setattr(settings, "registration_policy", "invite_only")

    # When: registration prepares both possible bootstrap and invited scopes.
    await prepare_registration(
        db,
        RegisterRequest(
            email="cross-company@example.com",
            password="correct horse",
            display_name=None,
            name="Cross company",
            invitation_token=SecretStr(token),
        ),
    )

    # Then: every company parent precedes every organization and invitation child.
    assert locked_tables == [
        "tenants",
        "tenants",
        "organizations",
        "organizations",
        "org_invitations",
    ]
