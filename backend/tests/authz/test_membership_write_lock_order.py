"""Membership writers acquire company scope before organization scope."""

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import Request
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.authz.invitation_queries import locked_invitation_scope
from app.authz.registration_queries import DEFAULT_COMPANY_ID, locked_default_organization
from app.authz.tenant_org_write_locks import (
    CURRENT_MEMBERSHIP_WRITERS,
    FUTURE_MEMBERSHIP_WRITER_CONTRACTS,
)
from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.organization_group import OrgInvitation
from app.schemas.auth import RegisterRequest
from app.services.registration_policy import prepare_registration
from app.services.registration_service import register_user

type InvitationScalarResult = Tenant | Organization | OrgInvitation | None
type RegistrationScalarResult = UUID | Tenant | Organization | OrgInvitation | None


def audit_request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/auth/register",
            "raw_path": b"/api/auth/register",
            "query_string": b"",
            "headers": [],
            "client": None,
            "server": ("test", 80),
            "state": {},
        }
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


async def test_full_invited_registration_never_relocks_a_parent_after_a_child(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = Tenant(
        id=DEFAULT_COMPANY_ID,
        name="Registration default",
        slug="registration-default",
        secrets_namespace="registration/default",
    )
    invited = Tenant(
        name="Registration invited",
        slug="registration-invited",
        secrets_namespace="registration/invited",
    )
    db.add_all([default, invited])
    await db.flush()
    default_org = Organization(
        tenant_id=default.id,
        name="Registration default org",
        slug="registration-default-org",
        is_default=True,
    )
    invited_org = Organization(
        tenant_id=invited.id,
        name="Registration invited org",
        slug="registration-invited-org",
    )
    db.add_all([default_org, invited_org])
    await db.flush()
    token = "full-registration-lock-order"
    invitation = OrgInvitation(
        org_id=invited_org.id,
        tenant_id=invited.id,
        email="full-registration@example.com",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db.add(invitation)
    await db.commit()
    lock_trace: list[str] = []
    lock_order_violations: list[tuple[str, str]] = []
    last_locked_table: str | None = None
    lock_rank = {"tenants": 0, "organizations": 1, "org_invitations": 2}
    original_scalar: Callable[[Executable], Awaitable[RegistrationScalarResult]] = db.scalar

    async def record_registration_locks(statement: Executable) -> RegistrationScalarResult:
        nonlocal last_locked_table
        result = await original_scalar(statement)
        sql = str(statement)
        if "FOR UPDATE" not in sql:
            return result
        table = next(
            (
                name
                for name in ("tenants", "organizations", "org_invitations")
                if f"FROM {name}" in sql
            ),
            None,
        )
        if table is None:
            return result
        lock_trace.append(table)
        if last_locked_table is not None and lock_rank[table] < lock_rank[last_locked_table]:
            lock_order_violations.append((last_locked_table, table))
        last_locked_table = table
        return result

    monkeypatch.setattr(db, "scalar", record_registration_locks)
    monkeypatch.setattr(settings, "allow_first_user_as_admin", True)
    monkeypatch.setattr(settings, "registration_policy", "invite_only")

    user = await register_user(
        db,
        RegisterRequest(
            email="full-registration@example.com",
            password="correct horse",
            display_name=None,
            name="Full registration",
            invitation_token=SecretStr(token),
        ),
        audit_request(),
    )
    await db.commit()

    assert lock_order_violations == []
    assert len(lock_trace) == 5
    assert lock_trace.count("tenants") == 2
    persisted = await db.get(OrgInvitation, invitation.id)
    assert persisted is not None and persisted.accepted_by == user.id
    assert await db.get(TenantMember, (invited.id, user.id)) is not None
    assert await db.get(OrganizationMember, (invited_org.id, user.id)) is not None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "org.invitation_accept",
                AuditEvent.target_id == str(invitation.id),
            )
        )
        == 1
    )


def test_membership_writer_inventory_covers_current_and_future_seams() -> None:
    # Given: current writers and the Task 7/8/9 consumer contracts.
    repository = Path(__file__).parents[3]

    # When: the checked inventory is evaluated.
    current = set(CURRENT_MEMBERSHIP_WRITERS)
    future = set(FUTURE_MEMBERSHIP_WRITER_CONTRACTS)

    # Then: every current writer exists while future contracts need no placeholder file.
    assert current == {
        "backend/app/services/orgs/members.py",
        "backend/app/services/orgs/roles.py",
        "backend/app/services/orgs/invitation_acceptance.py",
        "backend/app/services/orgs/invitation_membership.py",
        "backend/app/services/registration_policy.py",
        "backend/app/services/registration_membership.py",
    }
    assert all((repository / path).is_file() for path in current)
    required_seams = {
        "backend/app/services/orgs/members.py": (
            "members_write_context(",
            "lock_member_rows(",
            "preflight_member_exit(",
        ),
        "backend/app/services/orgs/roles.py": (
            "members_write_context(",
            ".order_by(OrganizationRoleGrant.id)",
            ".with_for_update()",
        ),
        "backend/app/services/orgs/invitation_acceptance.py": (
            "locked_invitation_scope(",
            "lock_member_rows(",
            "join_invited_membership(",
        ),
        "backend/app/services/orgs/invitation_membership.py": (
            "join_invited_membership(",
            'company.status != "active"',
        ),
        "backend/app/services/registration_policy.py": (
            "locked_registration_scope(",
            "include_default=settings.allow_first_user_as_admin",
        ),
        "backend/app/services/registration_membership.py": (
            "enrollment.scope.tenant",
            "ensure_signup_scope_active(enrollment.scope)",
        ),
    }
    for path, seams in required_seams.items():
        source = (repository / path).read_text()
        assert all(seam in source for seam in seams), path
    assert future == {
        "backend/app/services/tenants/organizations.py",
        "backend/app/services/tenants/members.py",
        "backend/app/services/tenants/roles.py",
        "backend/app/services/tenants/invitations.py",
    }
