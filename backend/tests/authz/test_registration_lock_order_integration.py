"""Real PostgreSQL trace for cross-company invited registration lock order."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import Executable

from app.authz.registration_queries import DEFAULT_COMPANY_ID
from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, Tenant
from app.models.organization_group import OrgInvitation
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.services.registration_service import register_user
from tests.authz.company_administration_migration_fixture import _runner_dsns

pytestmark = pytest.mark.integration

type RegistrationScalar = UUID | Tenant | Organization | OrgInvitation | None


def audit_request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/native-fixture",
            "raw_path": b"/native-fixture",
            "query_string": b"",
            "headers": [],
            "client": None,
            "server": ("test", 80),
            "state": {},
        }
    )


@pytest.fixture
async def native_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_runner_dsns().async_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_native_cross_company_invited_signup_locks_all_parents_before_children(
    native_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    key, token = uuid4().hex, f"native-registration-{uuid4().hex}"
    target_tenant_id: UUID
    target_org_id: UUID
    invitation_id: UUID
    created_default = False
    async with native_sessions() as db:
        default = await db.get(Tenant, DEFAULT_COMPANY_ID)
        if default is None:
            default = Tenant(
                id=DEFAULT_COMPANY_ID,
                name="Native default",
                slug=f"native-default-{key}",
                secrets_namespace=f"native/default/{key}",
            )
            db.add(default)
            await db.flush()
            db.add(
                Organization(
                    tenant_id=default.id,
                    name="Native default org",
                    slug=f"native-default-org-{key}",
                    is_default=True,
                )
            )
            created_default = True
        target = Tenant(
            name="Native invited",
            slug=f"native-invited-{key}",
            secrets_namespace=f"native/invited/{key}",
        )
        db.add(target)
        await db.flush()
        target_org = Organization(
            tenant_id=target.id,
            name="Native invited org",
            slug=f"native-invited-org-{key}",
        )
        db.add(target_org)
        await db.flush()
        invitation = OrgInvitation(
            org_id=target_org.id,
            tenant_id=target.id,
            email=f"native-registration-{key}@example.com",
            role="member",
            group_ids=[],
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add(invitation)
        await db.commit()
        target_tenant_id, target_org_id, invitation_id = target.id, target_org.id, invitation.id

    user_id: UUID | None = None
    call_phase_complete = False
    try:
        async with native_sessions() as db:
            locked_tables: list[str] = []
            original_scalar: Callable[[Executable], Awaitable[RegistrationScalar]] = db.scalar

            async def record_locked_table(statement: Executable) -> RegistrationScalar:
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
            user = await register_user(
                db,
                RegisterRequest(
                    email=f"native-registration-{key}@example.com",
                    password="correct horse",
                    display_name=None,
                    name="Native registration",
                    invitation_token=SecretStr(token),
                ),
                audit_request(),
            )
            user_id = user.id
            await db.commit()
            assert locked_tables == [
                "tenants",
                "tenants",
                "organizations",
                "organizations",
                "org_invitations",
            ]
            persisted = await db.get(OrgInvitation, invitation_id)
            assert persisted is not None and persisted.accepted_by == user.id
            call_phase_complete = True
    finally:
        try:
            async with native_sessions() as db:
                if user_id is not None:
                    await db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user_id))
                await db.execute(delete(OrgInvitation).where(OrgInvitation.id == invitation_id))
                if user_id is not None:
                    await db.execute(delete(User).where(User.id == user_id))
                await db.execute(delete(Organization).where(Organization.id == target_org_id))
                await db.execute(delete(Tenant).where(Tenant.id == target_tenant_id))
                if created_default:
                    await db.execute(
                        delete(Organization).where(Organization.tenant_id == DEFAULT_COMPANY_ID)
                    )
                    await db.execute(delete(Tenant).where(Tenant.id == DEFAULT_COMPANY_ID))
                await db.commit()
        except IntegrityError as error:
            raise AssertionError(
                "native_registration_fixture_cleanup_integrity_error:"
                f"call_phase_complete={call_phase_complete}"
            ) from error
