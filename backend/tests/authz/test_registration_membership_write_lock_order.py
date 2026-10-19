"""Registration-specific membership writer lock-order test."""

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.authz.registration_queries import DEFAULT_COMPANY_ID
from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.organization_group import OrgInvitation
from app.schemas.auth import RegisterRequest
from app.services.registration_service import register_user
from tests.authz.membership_write_lock_fixture import RegistrationScalarResult, audit_request


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
