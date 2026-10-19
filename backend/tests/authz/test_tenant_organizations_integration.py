"""Native PostgreSQL organization creation invariants."""

from __future__ import annotations

import hashlib
from uuid import UUID

import anyio
import pytest
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrganizationActor
from app.models.audit_event import AuditEvent
from app.models.organization import OrganizationMember, OrganizationRoleGrant
from app.models.organization_group import OrgInvitation
from app.services.tenants import organizations as organization_service
from app.tenancy.admin_context import TenantAdminContext, TenantAdminTarget
from tests.authz.tenant_organizations_native_barriers import NativeLockBarrier
from tests.authz.tenant_organizations_native_fixture import (
    NativeTask7Sessions,
    native_task7_client,
    seed_native_task7,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("native_task7_cleanup_guard")]
pytest_plugins = ["tests.authz.tenant_organizations_native_fixture"]


async def test_concurrent_same_name_creates_allocate_unique_stable_slugs(
    native_task7_sessions: NativeTask7Sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with native_task7_client(native_task7_sessions) as client:
        case = await seed_native_task7(native_task7_sessions, client)
        responses: dict[str, Response] = {}
        barrier = NativeLockBarrier()
        original = organization_service.load_context

        async def gated_load(
            db: AsyncSession, actor: OrganizationActor, target: TenantAdminTarget
        ) -> TenantAdminContext:
            ordinal = await barrier.before_attempt(db)
            context = await original(db, actor, target)
            await barrier.after_actual_lock(ordinal)
            return context

        monkeypatch.setattr(organization_service, "load_context", gated_load)

        async def create(label: str) -> None:
            responses[label] = await client.post(
                f"/api/tenants/{case.tenant_id}/organizations",
                json={"name": "Parallel organization"},
                headers=case.auth.headers(),
            )

        with anyio.fail_after(15):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(create, "first")
                await barrier.first_acquired.wait()
                tasks.start_soon(create, "second")
                blocker, blocked = await barrier.wait_for_second_blocked(
                    native_task7_sessions.observer
                )
                assert blocker != blocked
                barrier.release()
        assert sorted(response.status_code for response in responses.values()) == [201, 201]
        slugs = sorted(response.json()["organization"]["slug"] for response in responses.values())
        assert slugs == [
            "parallel-organization",
            "parallel-organization-2",
        ]
        async with native_task7_sessions.observer() as db:
            actor_members = await db.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(OrganizationMember.user_id == case.actor_id)
            )
            create_audits = await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.actor_user_id == case.actor_id,
                    AuditEvent.action == "org.create",
                )
            )
        assert actor_members == 0
        assert create_audits == 2


async def test_member_and_email_administrator_creation_persist_atomically(
    native_task7_sessions: NativeTask7Sessions,
) -> None:
    async with native_task7_client(native_task7_sessions) as client:
        case = await seed_native_task7(native_task7_sessions, client)
        member = await client.post(
            f"/api/tenants/{case.tenant_id}/organizations",
            json={"name": "Member admin", "administrator": {"user_id": str(case.target_id)}},
            headers=case.auth.headers(),
        )
        invited = await client.post(
            f"/api/tenants/{case.tenant_id}/organizations",
            json={"name": "Invite admin", "administrator": {"email": "native@example.com"}},
            headers=case.auth.headers(),
        )
        assert member.status_code == invited.status_code == 201
        invitation = invited.json()["administrator"]["invitation"]
        async with native_task7_sessions.observer() as db:
            role = await db.scalar(
                select(OrganizationRoleGrant.role).where(
                    OrganizationRoleGrant.org_id == UUID(member.json()["organization"]["id"]),
                    OrganizationRoleGrant.principal_id == case.target_id,
                )
            )
            row = await db.get(OrgInvitation, UUID(invitation["id"]))
        assert role == "admin"
        assert row is not None
        assert row.token_hash == hashlib.sha256(invitation["token"].encode()).hexdigest()
        assert invitation["token"] != row.token_hash
