"""Real PostgreSQL queues genuine B04 HTTP writes behind the Tenant parent."""

from __future__ import annotations

from typing import Literal

import anyio
import pytest
from httpx import Response
from sqlalchemy import func, select

from app.models.audit_event import AuditEvent
from app.models.organization import OrganizationMember, Tenant, TenantMember
from app.services.tenants.member_exit import TenantMemberExitRequest, exit_tenant_member
from tests.authz.b04_write_native_fixture import (
    NativeB04Case,
    NativeB04Sessions,
    cleanup_native_b04,
    native_b04_client,
    seed_native_b04,
    wait_for_lock_wait,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("native_b04_cleanup_guard"),
]
pytest_plugins = ["tests.authz.b04_write_native_fixture"]

Winner = Literal["add", "exit"]


async def run_company_exit(sessions: NativeB04Sessions, case: NativeB04Case) -> None:
    async with sessions.exit() as db:
        await exit_tenant_member(
            TenantMemberExitRequest(db, case.tenant_id, case.target_id, case.actor)
        )
        await db.commit()


async def assert_final_state(
    sessions: NativeB04Sessions,
    case: NativeB04Case,
    *,
    add_audits: int,
    remove_audits: int,
) -> None:
    async with sessions.observer() as db:
        company_member = await db.get(TenantMember, (case.tenant_id, case.target_id))
        active_orgs = await db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(
                OrganizationMember.tenant_id == case.tenant_id,
                OrganizationMember.user_id == case.target_id,
                OrganizationMember.status == "active",
            )
        )
        add_count = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.target_id == str(case.target_id),
                AuditEvent.action == "org.member_add",
            )
        )
        remove_count = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.target_id == str(case.target_id),
                AuditEvent.action == "org.member_remove",
            )
        )
        tenant_remove_count = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.target_id == str(case.target_id),
                AuditEvent.action == "tenant.member_remove",
            )
        )
    assert company_member is not None and company_member.status == "removed"
    assert active_orgs == 0
    assert add_count == add_audits
    assert remove_count == remove_audits
    assert tenant_remove_count == 1


@pytest.mark.parametrize("winner", ["add", "exit"])
async def test_real_http_add_serializes_with_company_exit(
    native_b04_sessions: NativeB04Sessions,
    winner: Winner,
) -> None:
    # Given: a target belongs to the company and one org, while a second org is unaffected.
    case: NativeB04Case | None = None
    async with native_b04_client(native_b04_sessions) as client:
        case = await seed_native_b04(native_b04_sessions, client)
        listed = await client.get(f"/api/orgs/{case.add_org_id}/members")
        assert listed.status_code == 200
        headers = {**case.auth.headers(), "If-Match": listed.headers["etag"]}
        responses: list[Response] = []

        async def add_member() -> None:
            responses.append(
                await client.post(
                    f"/api/orgs/{case.add_org_id}/members",
                    json={"user_id": str(case.target_id)},
                    headers=headers,
                )
            )

        gate = native_b04_sessions.observer()
        try:
            await gate.begin()
            locked = await gate.scalar(
                select(Tenant).where(Tenant.id == case.tenant_id).with_for_update()
            )
            assert locked is not None

            # When: both genuine writers queue on the same held Tenant lock in a fixed order.
            with anyio.fail_after(15):
                async with anyio.create_task_group() as tasks:
                    if winner == "add":
                        tasks.start_soon(add_member)
                        await wait_for_lock_wait(native_b04_sessions, "b04_http")
                        tasks.start_soon(run_company_exit, native_b04_sessions, case)
                        await wait_for_lock_wait(native_b04_sessions, "b04_exit")
                    else:
                        tasks.start_soon(run_company_exit, native_b04_sessions, case)
                        await wait_for_lock_wait(native_b04_sessions, "b04_exit")
                        tasks.start_soon(add_member)
                        await wait_for_lock_wait(native_b04_sessions, "b04_http")
                    await gate.commit()
        finally:
            await gate.close()

        # Then: both finish without deadlock and the queued winner determines the bounded outcome.
        assert len(responses) == 1
        if winner == "add":
            assert responses[0].status_code == 201
            await assert_final_state(native_b04_sessions, case, add_audits=1, remove_audits=2)
        else:
            assert responses[0].status_code == 404
            assert responses[0].json()["error"]["code"] == "not_found"
            await assert_final_state(native_b04_sessions, case, add_audits=0, remove_audits=1)
    if case is not None:
        await cleanup_native_b04(native_b04_sessions, case)
