"""Native PostgreSQL company-admin replacement races."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from typing import Literal
from uuid import UUID

import anyio
import pytest
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import org_control
from app.authz.error_codes import ErrorCode
from app.authz.tenant_org_write_locks import TenantOrgWriteLock
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, OrganizationMember, OrganizationRoleGrant
from tests.authz.tenant_organizations_native_barriers import NativeLockBarrier
from tests.authz.tenant_organizations_native_fixture import (
    NativeTask7Sessions,
    native_task7_client,
    seed_native_task7,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("native_task7_cleanup_guard")]
pytest_plugins = ["tests.authz.tenant_organizations_native_fixture"]

FirstWriter = Literal["promote", "remove"]


@pytest.mark.parametrize("first_writer", ["promote", "remove"])
async def test_concurrent_replacement_and_last_admin_removal_are_serialized(
    native_task7_sessions: NativeTask7Sessions,
    monkeypatch: pytest.MonkeyPatch,
    first_writer: FirstWriter,
) -> None:
    async with native_task7_client(native_task7_sessions) as client:
        case = await seed_native_task7(native_task7_sessions, client)
        before = await client.get(f"/api/orgs/{case.org_id}/roles")
        assert before.status_code == 200
        initial_etag = before.headers["etag"]
        async with native_task7_sessions.observer() as db:
            organization = await db.get(Organization, case.org_id)
            assert organization is not None
            initial_epoch = organization.authz_epoch
            initial_settings = organization.settings
            audit_before = Counter(
                await db.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.actor_user_id == case.actor_id,
                        AuditEvent.action.in_(("org.role_grant", "org.role_revoke")),
                    )
                )
            )

        barrier = NativeLockBarrier()
        original_lock = org_control.lock_tenant_org_write

        async def gated_lock(
            db: AsyncSession,
            tenant_id: UUID,
            organization_ids: Collection[UUID] | None,
            missing_code: ErrorCode = "not_found",
        ) -> TenantOrgWriteLock:
            ordinal = await barrier.before_attempt(db)
            locked = await original_lock(db, tenant_id, organization_ids, missing_code)
            await barrier.after_actual_lock(ordinal)
            return locked

        monkeypatch.setattr(org_control, "lock_tenant_org_write", gated_lock)
        responses: dict[str, Response] = {}

        async def replace(label: str) -> None:
            principal = case.replacement_id if label == "promote" else case.target_id
            roles = ["admin"] if label == "promote" else []
            responses[label] = await client.put(
                f"/api/orgs/{case.org_id}/roles",
                json={"principal": {"type": "user", "id": str(principal)}, "roles": roles},
                headers={**case.auth.headers(), "If-Match": initial_etag},
            )

        second = "remove" if first_writer == "promote" else "promote"
        with anyio.fail_after(15):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(replace, first_writer)
                await barrier.first_acquired.wait()
                tasks.start_soon(replace, second)
                blocker, blocked = await barrier.wait_for_second_blocked(
                    native_task7_sessions.observer
                )
                assert blocker != blocked
                barrier.release()
        monkeypatch.setattr(org_control, "lock_tenant_org_write", original_lock)

        assert responses["promote"].status_code == 200
        assert responses["remove"].status_code == 409
        expected = "stale_version" if first_writer == "promote" else "last_admin"
        assert responses["remove"].json()["error"]["code"] == expected
        current = await client.get(f"/api/orgs/{case.org_id}/roles")
        assert current.status_code == 200
        assert current.headers["etag"] != initial_etag
        removed = await client.put(
            f"/api/orgs/{case.org_id}/roles",
            json={"principal": {"type": "user", "id": str(case.target_id)}, "roles": []},
            headers={**case.auth.headers(), "If-Match": current.headers["etag"]},
        )
        assert removed.status_code == 200
        assert removed.headers["etag"] != current.headers["etag"]

        async with native_task7_sessions.observer() as db:
            organization = await db.get(Organization, case.org_id)
            assert organization is not None
            grants = set(
                (
                    await db.execute(
                        select(
                            OrganizationRoleGrant.principal_id, OrganizationRoleGrant.role
                        ).where(OrganizationRoleGrant.org_id == case.org_id)
                    )
                )
                .tuples()
                .all()
            )
            members = dict(
                (
                    await db.execute(
                        select(OrganizationMember.user_id, OrganizationMember.status).where(
                            OrganizationMember.org_id == case.org_id
                        )
                    )
                )
                .tuples()
                .all()
            )
            audit_after = Counter(
                await db.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.actor_user_id == case.actor_id,
                        AuditEvent.action.in_(("org.role_grant", "org.role_revoke")),
                    )
                )
            )
        assert grants == {(case.replacement_id, "admin"), (case.unrelated_id, "auditor")}
        assert members == {
            case.target_id: "active",
            case.replacement_id: "active",
            case.unrelated_id: "active",
        }
        assert organization.settings == initial_settings
        # Two versioned writes, and the revocation's role marker bumps once more (8.6).
        assert organization.authz_epoch == initial_epoch + 3
        assert audit_after - audit_before == Counter({"org.role_grant": 1, "org.role_revoke": 1})
