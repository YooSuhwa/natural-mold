"""Real PostgreSQL/HTTP races for every final company-admin self mutation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Literal, assert_never
from uuid import UUID

import anyio
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.tenant_org_write_locks import TenantOrgWriteLock
from app.models.audit_event import AuditEvent
from app.models.organization import (
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.services.tenants import member_write
from app.services.tenants.versioning import etag
from tests.authz.tenant_members_native_fixture import (
    NativeMemberRace,
    NativeSessions,
    assert_blocked,
    login,
    native_clients,
    seed_admin_race,
)

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_members_native_fixture"]
Operation = Literal["demote", "clear", "suspend", "delete"]
LockDelegate = Callable[
    [AsyncSession, UUID, Collection[UUID] | None], Awaitable[TenantOrgWriteLock]
]


class LockProbe:
    def __init__(self) -> None:
        self.first_pid: int | None = None
        self.second_pid: int | None = None
        self.first_locked = anyio.Event()
        self.second_entered = anyio.Event()
        self.release_first = anyio.Event()


@dataclass(frozen=True, slots=True)
class NativeState:
    statuses: dict[UUID, str]
    roles: dict[UUID, tuple[str, ...]]
    effective_admins: int
    target_org_statuses: tuple[str, ...]
    target_org_role_grants: int
    tenant_etag: str
    audits: dict[str, int]


async def read_state(sessions: NativeSessions, case: NativeMemberRace) -> NativeState:
    actor_ids = tuple(actor.id for actor in case.actors)
    async with sessions.observer() as db:
        members = tuple(
            await db.scalars(select(TenantMember).where(TenantMember.tenant_id == case.tenant_id))
        )
        grants = tuple(
            await db.scalars(
                select(TenantRoleGrant).where(TenantRoleGrant.tenant_id == case.tenant_id)
            )
        )
        tenant = await db.get(Tenant, case.tenant_id)
        audits = (
            await db.execute(
                select(AuditEvent.action).where(
                    AuditEvent.target_id.in_(tuple(str(value) for value in actor_ids))
                )
            )
        ).all()
        target_org_members = tuple(
            await db.scalars(
                select(OrganizationMember).where(
                    OrganizationMember.tenant_id == case.tenant_id,
                    OrganizationMember.user_id == actor_ids[0],
                )
            )
        )
        target_org_grants = tuple(
            await db.scalars(
                select(OrganizationRoleGrant).where(
                    OrganizationRoleGrant.tenant_id == case.tenant_id,
                    OrganizationRoleGrant.principal_type == "user",
                    OrganizationRoleGrant.principal_id == actor_ids[0],
                )
            )
        )
    assert tenant is not None
    active = {member.user_id for member in members if member.status == "active"}
    actions = {row[0] for row in audits if row[0].startswith(("tenant.", "org."))}
    return NativeState(
        {member.user_id: member.status for member in members},
        {uid: tuple(sorted(row.role for row in grants if row.user_id == uid)) for uid in actor_ids},
        len({row.user_id for row in grants if row.role == "admin" and row.user_id in active}),
        tuple(sorted(member.status for member in target_org_members)),
        len(target_org_grants),
        etag(tenant.updated_at),
        {action: sum(1 for row in audits if row[0] == action) for action in actions},
    )


def instrumented_lock(probe: LockProbe, delegate: LockDelegate) -> LockDelegate:
    async def lock(
        db: AsyncSession, tenant_id: UUID, organization_ids: Collection[UUID] | None
    ) -> TenantOrgWriteLock:
        name = await db.scalar(text("SELECT current_setting('application_name')"))
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        if name == "task8_member_first":
            probe.first_pid = pid
            result = await delegate(db, tenant_id, organization_ids)
            probe.first_locked.set()
            await probe.release_first.wait()
            return result
        assert name == "task8_member_second"
        probe.second_pid = pid
        probe.second_entered.set()
        return await delegate(db, tenant_id, organization_ids)

    return lock


async def mutate(
    client: AsyncClient,
    case: NativeMemberRace,
    actor_index: int,
    csrf: str,
    version: str,
    operation: Operation,
) -> Response:
    actor = case.actors[actor_index]
    headers = {"X-CSRF-Token": csrf, "If-Match": version}
    match operation:
        case "demote":
            return await client.put(
                f"/api/tenants/{case.tenant_id}/roles/{actor.id}",
                json={"roles": ["auditor"]},
                headers=headers,
            )
        case "clear":
            return await client.put(
                f"/api/tenants/{case.tenant_id}/roles/{actor.id}",
                json={"roles": []},
                headers=headers,
            )
        case "suspend":
            return await client.patch(
                f"/api/tenants/{case.tenant_id}/members/{actor.id}",
                json={"status": "suspended"},
                headers=headers,
            )
        case "delete":
            return await client.delete(
                f"/api/tenants/{case.tenant_id}/members/{actor.id}", headers=headers
            )
        case _ as unreachable:
            assert_never(unreachable)


def expected_audits(operation: Operation) -> dict[str, int]:
    match operation:
        case "demote":
            return {"tenant.role_grant": 1, "tenant.role_revoke": 1}
        case "clear":
            return {"tenant.role_revoke": 1}
        case "suspend":
            return {"tenant.member_suspend": 1}
        case "delete":
            return {"org.member_remove": 2, "tenant.member_remove": 1}
        case _ as unreachable:
            assert_never(unreachable)


def assert_operation_state(
    operation: Operation, case: NativeMemberRace, state: NativeState
) -> None:
    target, survivor = (actor.id for actor in case.actors)
    assert state.statuses[survivor] == "active"
    assert state.roles[survivor] == ("admin",)
    assert state.effective_admins == 1
    assert state.audits == expected_audits(operation)
    match operation:
        case "demote":
            assert state.statuses[target] == "active"
            assert state.roles[target] == ("auditor",)
            assert state.target_org_statuses == ("active", "active")
            assert state.target_org_role_grants == 2
        case "clear":
            assert state.statuses[target] == "active"
            assert state.roles[target] == ()
            assert state.target_org_statuses == ("active", "active")
            assert state.target_org_role_grants == 2
        case "suspend":
            assert state.statuses[target] == "suspended"
            assert state.roles[target] == ("admin",)
            assert state.target_org_statuses == ("active", "active")
            assert state.target_org_role_grants == 2
        case "delete":
            assert state.statuses[target] == "removed"
            assert state.roles[target] == ()
            assert state.target_org_statuses == ("removed", "removed")
            assert state.target_org_role_grants == 0
        case _ as unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", ["demote", "clear", "suspend", "delete"])
async def test_two_admin_self_mutations_lock_serialize_http_and_preserve_last_admin(
    tenant_member_native_sessions: NativeSessions,
    monkeypatch: pytest.MonkeyPatch,
    operation: Operation,
) -> None:
    sessions = tenant_member_native_sessions
    case = await seed_admin_race(sessions)
    probe = LockProbe()
    monkeypatch.setattr(
        member_write,
        "lock_tenant_org_write",
        instrumented_lock(probe, member_write.lock_tenant_org_write),
    )
    async with native_clients(sessions) as (first, second):
        csrf = (await login(first, case.actors[0]), await login(second, case.actors[1]))
        responses: list[Response] = []

        async def first_writer() -> None:
            responses.append(await mutate(first, case, 0, csrf[0], case.initial_etag, operation))

        async def second_writer() -> None:
            responses.append(await mutate(second, case, 1, csrf[1], case.initial_etag, operation))

        with anyio.fail_after(15):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(first_writer)
                await probe.first_locked.wait()
                tasks.start_soon(second_writer)
                await probe.second_entered.wait()
                assert probe.first_pid is not None and probe.second_pid is not None
                assert probe.first_pid != probe.second_pid
                await assert_blocked(sessions, probe.second_pid, probe.first_pid)
                probe.release_first.set()

        success = 204 if operation == "delete" else 200
        assert sorted(response.status_code for response in responses) == [success, 409]
        winner = next(response for response in responses if response.status_code == success)
        stale = next(response for response in responses if response.status_code == 409)
        assert stale.json()["error"]["code"] == "stale_version"
        after_commit = await read_state(sessions, case)
        assert after_commit.tenant_etag != case.initial_etag
        assert winner.headers["etag"] == after_commit.tenant_etag
        assert_operation_state(operation, case, after_commit)

        retry = await mutate(second, case, 1, csrf[1], after_commit.tenant_etag, operation)
        assert retry.status_code == 409
        assert retry.json()["error"]["code"] == "last_tenant_admin"
        final = await read_state(sessions, case)
        assert final == after_commit
