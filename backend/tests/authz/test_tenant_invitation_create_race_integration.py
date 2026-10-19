"""PostgreSQL serializes identical company invitation HTTP creators."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING

import anyio
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.organization import Tenant
from app.models.tenant_invitation import TenantInvitation
from app.services.tenants import invitations
from app.services.tenants.versioning import etag
from app.tenancy.admin_context import TenantAdminContext, TenantAdminTarget
from tests.authz.tenant_invitation_native_barriers import NativeLockBarrier
from tests.authz.tenant_invitation_native_fixture import (
    invitation_audit_count,
    login_native,
    native_company,
)

if TYPE_CHECKING:
    from tests.authz.tenant_invitation_native_fixture import NativeInvitationDatabase

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.tenant_invitation_native_fixture"]


async def test_identical_http_creates_lock_then_return_one_row_and_token(
    tenant_invitation_native_database: NativeInvitationDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tenant_invitation_native_database
    async with native_company(
        database.primary, invited_email="native-create@example.com"
    ) as fixture:
        responses: list[Response] = []
        async with database.primary() as db:
            tenant = await db.get(Tenant, fixture.tenant_id)
            assert tenant is not None
            initial_updated_at = tenant.updated_at
            initial_etag = etag(tenant.updated_at)
        async with (
            database.client(database.primary) as first_client,
            database.client(database.competing) as second_client,
        ):
            first_auth = await login_native(first_client, fixture.actor.email)
            second_auth = await login_native(second_client, fixture.actor.email)
            first_auth.apply(first_client)
            second_auth.apply(second_client)
            barrier = NativeLockBarrier()
            real_lock = invitations.load_tenant_admin_context

            async def locked_context(
                db: AsyncSession, actor: CurrentUser, target: TenantAdminTarget
            ) -> TenantAdminContext:
                return await barrier.run(db, real_lock, db, actor, target)

            monkeypatch.setattr(invitations, "load_tenant_admin_context", locked_context)
            path = f"/api/tenants/{fixture.tenant_id}/invitations"
            payload = {"email": fixture.invited_email, "roles": ["admin"]}

            async def create(client: AsyncClient, csrf: str) -> None:
                responses.append(
                    await client.post(
                        path,
                        json=payload,
                        headers={"X-CSRF-Token": csrf, "If-Match": initial_etag},
                    )
                )

            with anyio.fail_after(20):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(create, first_client, first_auth.csrf)
                    await barrier.first_acquired.wait()
                    tasks.start_soon(create, second_client, second_auth.csrf)
                    await barrier.prove_second_blocked(database.primary)
                    barrier.release()
        assert sorted(response.status_code for response in responses) == [200, 201]
        bodies = [response.json() for response in responses]
        assert len({body["id"] for body in bodies}) == 1
        assert len({body["token"] for body in bodies}) == 1
        token = bodies[0]["token"]
        async with database.primary() as db:
            rows = list(
                (
                    await db.scalars(
                        select(TenantInvitation).where(
                            TenantInvitation.tenant_id == fixture.tenant_id
                        )
                    )
                ).all()
            )
            tenant = await db.get(Tenant, fixture.tenant_id)
            assert tenant is not None and tenant.updated_at != initial_updated_at
            final_etag = etag(tenant.updated_at)
        assert {response.headers["etag"] for response in responses} == {final_etag}
        assert len(rows) == 1
        row = rows[0]
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert row.expires_at - row.created_at == timedelta(days=7)
        assert row.status == "pending"
        assert row.accepted_at is None and row.revoked_at is None
        assert (
            await invitation_audit_count(
                database.primary, fixture.tenant_id, "tenant.invitation_create"
            )
            == 1
        )
