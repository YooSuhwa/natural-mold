"""A company invitation HTTP acceptance is consumed once under contention."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.models.organization import OrganizationMember, Tenant, TenantMember, TenantRoleGrant
from app.models.tenant_invitation import TenantInvitation
from app.services.tenants import invitation_acceptance
from tests.authz.tenant_invitation_native_barriers import NativeLockBarrier
from tests.authz.tenant_invitation_native_fixture import (
    invitation_audit_count,
    login_native,
    native_company,
    seed_native_invitation,
)

if TYPE_CHECKING:
    from tests.authz.tenant_invitation_native_fixture import NativeInvitationDatabase

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_invitation_native_fixture"]


async def test_two_http_acceptors_lock_then_commit_one_membership_and_role(
    tenant_invitation_native_database: NativeInvitationDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tenant_invitation_native_database
    async with native_company(
        database.primary, invited_email="native-accept@example.com"
    ) as fixture:
        invitation, token = await seed_native_invitation(database.primary, fixture)
        responses: list[Response] = []
        async with (
            database.client(database.primary) as first_client,
            database.client(database.competing) as second_client,
        ):
            first_auth = await login_native(first_client, fixture.invited_email)
            second_auth = await login_native(second_client, fixture.invited_email)
            first_auth.apply(first_client)
            second_auth.apply(second_client)
            barrier = NativeLockBarrier()
            real_lock = invitation_acceptance.locked_invitation_by_digest

            async def locked_invitation(
                db: AsyncSession, digest: str
            ) -> tuple[Tenant, TenantInvitation]:
                return await barrier.run(db, real_lock, db, digest)

            monkeypatch.setattr(
                invitation_acceptance, "locked_invitation_by_digest", locked_invitation
            )

            async def accept(client: AsyncClient, csrf: str) -> None:
                responses.append(
                    await client.post(
                        "/api/invitations/accept",
                        json={"token": token},
                        headers={"X-CSRF-Token": csrf},
                    )
                )

            with anyio.fail_after(20):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(accept, first_client, first_auth.csrf)
                    await barrier.first_acquired.wait()
                    tasks.start_soon(accept, second_client, second_auth.csrf)
                    await barrier.prove_second_blocked(database.primary)
                    barrier.release()
        assert sorted(response.status_code for response in responses) == [200, 409]
        failed = next(response for response in responses if response.status_code == 409)
        assert failed.json()["error"]["code"] == "tenant_invitation_used"
        succeeded = next(response for response in responses if response.status_code == 200)
        assert succeeded.json()["org_id"] is None
        assert ACTIVE_ORG_COOKIE not in succeeded.cookies
        assert token not in succeeded.text
        async with database.primary() as db:
            row = await db.get(TenantInvitation, invitation.id)
            member = await db.get(TenantMember, (fixture.tenant_id, fixture.invited_user_id))
            role_count = await db.scalar(
                select(func.count())
                .select_from(TenantRoleGrant)
                .where(
                    TenantRoleGrant.tenant_id == fixture.tenant_id,
                    TenantRoleGrant.user_id == fixture.invited_user_id,
                    TenantRoleGrant.role == "admin",
                )
            )
            org_count = await db.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(OrganizationMember.user_id == fixture.invited_user_id)
            )
        assert row is not None and row.status == "accepted"
        assert row.accepted_by == fixture.invited_user_id and row.accepted_at is not None
        assert member is not None and member.status == "active"
        assert role_count == 1 and org_count == 0
        for action in (
            "tenant.invitation_accept",
            "tenant.member_add",
            "tenant.role_grant",
        ):
            assert await invitation_audit_count(database.primary, fixture.tenant_id, action) == 1
