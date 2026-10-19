"""Company invitation acceptance and removal race through actual HTTP."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import anyio
import pytest
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.models.tenant_invitation import TenantInvitation
from app.services.tenants import invitation_acceptance, members
from app.services.tenants.member_write import TenantMemberWrite, TenantMemberWriteContext
from app.services.tenants.versioning import etag
from tests.authz.tenant_invitation_native_barriers import NativeLockBarrier
from tests.authz.tenant_invitation_native_fixture import (
    invitation_audit_count,
    login_native,
    native_company,
    seed_native_invitation,
)

if TYPE_CHECKING:
    from tests.authz.tenant_invitation_native_fixture import (
        NativeInvitationCompany,
        NativeInvitationDatabase,
    )

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_invitation_native_fixture"]


async def _run_race(
    database: NativeInvitationDatabase,
    fixture: NativeInvitationCompany,
    monkeypatch: pytest.MonkeyPatch,
    *,
    first: Literal["accept", "remove"],
) -> tuple[NativeInvitationCompany, TenantInvitation, list[Response]]:
    invitation, token = await seed_native_invitation(database.primary, fixture)
    async with database.primary() as db:
        db.add(TenantMember(tenant_id=fixture.tenant_id, user_id=fixture.invited_user_id))
        tenant = await db.get(Tenant, fixture.tenant_id)
        assert tenant is not None
        current_etag = etag(tenant.updated_at)
        await db.commit()
    responses: list[Response] = []
    async with (
        database.client(database.primary) as admin_client,
        database.client(database.competing) as invitee_client,
    ):
        admin_auth = await login_native(admin_client, fixture.actor.email)
        invitee_auth = await login_native(invitee_client, fixture.invited_email)
        admin_auth.apply(admin_client)
        invitee_auth.apply(invitee_client)
        barrier = NativeLockBarrier()
        real_accept_lock = invitation_acceptance.locked_invitation_by_digest
        real_remove_lock = members.lock_tenant_member_write

        async def accept_lock(db: AsyncSession, digest: str) -> tuple[Tenant, TenantInvitation]:
            return await barrier.run(db, real_accept_lock, db, digest)

        async def remove_lock(
            command: TenantMemberWrite, *, lock_children: bool
        ) -> TenantMemberWriteContext:
            return await barrier.run(
                command.db, real_remove_lock, command, lock_children=lock_children
            )

        monkeypatch.setattr(invitation_acceptance, "locked_invitation_by_digest", accept_lock)
        monkeypatch.setattr(members, "lock_tenant_member_write", remove_lock)

        async def accept() -> None:
            responses.append(
                await invitee_client.post(
                    "/api/invitations/accept",
                    json={"token": token},
                    headers={"X-CSRF-Token": invitee_auth.csrf},
                )
            )

        async def remove() -> None:
            responses.append(
                await admin_client.delete(
                    f"/api/tenants/{fixture.tenant_id}/members/{fixture.invited_user_id}",
                    headers={
                        "X-CSRF-Token": admin_auth.csrf,
                        "If-Match": current_etag,
                    },
                )
            )

        first_call, second_call = (accept, remove) if first == "accept" else (remove, accept)
        with anyio.fail_after(20):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(first_call)
                await barrier.first_acquired.wait()
                tasks.start_soon(second_call)
                await barrier.prove_second_blocked(database.primary)
                barrier.release()
    return fixture, invitation, responses


async def test_remove_first_blocks_acceptance_and_preserves_pending_invite(
    tenant_invitation_native_database: NativeInvitationDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tenant_invitation_native_database
    async with native_company(
        database.primary, invited_email="native-remove-first@example.com"
    ) as fixture:
        fixture, invitation, responses = await _run_race(
            database, fixture, monkeypatch, first="remove"
        )
        assert sorted(response.status_code for response in responses) == [204, 403]
        denied = next(response for response in responses if response.status_code == 403)
        assert denied.json()["error"]["code"] == "invitation_membership_blocked"
        async with database.primary() as db:
            member = await db.get(TenantMember, (fixture.tenant_id, fixture.invited_user_id))
            row = await db.get(TenantInvitation, invitation.id)
        assert member is not None and member.status == "removed"
        assert row is not None and row.status == "pending" and row.accepted_at is None
        assert (
            await invitation_audit_count(
                database.primary, fixture.tenant_id, "tenant.invitation_accept"
            )
            == 0
        )


async def test_accept_first_commits_history_then_removal_wins_final_state(
    tenant_invitation_native_database: NativeInvitationDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tenant_invitation_native_database
    async with native_company(
        database.primary, invited_email="native-accept-first@example.com"
    ) as fixture:
        fixture, invitation, responses = await _run_race(
            database, fixture, monkeypatch, first="accept"
        )
        assert sorted(response.status_code for response in responses) == [200, 204]
        async with database.primary() as db:
            member = await db.get(TenantMember, (fixture.tenant_id, fixture.invited_user_id))
            row = await db.get(TenantInvitation, invitation.id)
            roles = await db.scalar(
                select(func.count())
                .select_from(TenantRoleGrant)
                .where(
                    TenantRoleGrant.tenant_id == fixture.tenant_id,
                    TenantRoleGrant.user_id == fixture.invited_user_id,
                )
            )
        assert member is not None and member.status == "removed"
        assert row is not None and row.status == "accepted" and row.accepted_at is not None
        assert roles == 0
        assert (
            await invitation_audit_count(
                database.primary, fixture.tenant_id, "tenant.invitation_accept"
            )
            == 1
        )
        assert (
            await invitation_audit_count(
                database.primary, fixture.tenant_id, "tenant.member_remove"
            )
            == 1
        )
