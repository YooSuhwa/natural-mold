"""Actual B11 PostgreSQL verifies scoped inventory and concurrent invitation retries."""

from collections.abc import AsyncGenerator
from uuid import UUID

import anyio
import pytest
from anyio.to_thread import run_sync
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import Uuid, column, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.dependencies import get_db
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.model import Model
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
)
from app.models.organization_group import OrgInvitation
from app.models.tool import Tool
from app.schemas.auth import UserResponse
from app.services import audit_service
from tests.authz.organization_migration_fixture import async_organization_lab
from tests.conftest import register_session

pytestmark = pytest.mark.integration


async def test_live_catalog_scope_and_serialized_invitation_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with async_organization_lab() as lab:
        await run_sync(command.upgrade, lab.config, "b11_company_administration")
        engine = create_async_engine(lab.engine.url.set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            app = create_app()

            async def database_session() -> AsyncGenerator[AsyncSession, None]:
                async with sessions() as db:
                    yield db

            app.dependency_overrides[get_db] = database_session
            monkeypatch.setattr(audit_service, "async_session", sessions)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                login = await register_session(
                    client, email="pg-catalog@example.com", name="Catalog"
                )
                user_id = UserResponse.model_validate(login.body["user"]).id
                async with sessions() as db, db.begin():
                    company = Tenant(name="Catalog", slug="catalog", secrets_namespace="catalog")
                    other = Tenant(name="Other", slug="other", secrets_namespace="other")
                    db.add_all([company, other])
                    await db.flush()
                    organization = Organization(
                        tenant_id=company.id, name="Catalog", slug="catalog"
                    )
                    db.add(organization)
                    await db.flush()
                    db.add(TenantMember(tenant_id=company.id, user_id=user_id))
                    db.add(
                        OrganizationMember(
                            org_id=organization.id, tenant_id=company.id, user_id=user_id
                        )
                    )
                    db.add(
                        OrganizationRoleGrant(
                            org_id=organization.id,
                            tenant_id=company.id,
                            role="admin",
                            principal_type="user",
                            principal_id=user_id,
                        )
                    )
                    models = [
                        Model(provider="openai", model_name=name, display_name=name)
                        for name in ("Local", "Foreign")
                    ]
                    tool = Tool(
                        name="System", definition_key="builtin:current_datetime", is_system=True
                    )
                    db.add_all([*models, tool])
                    await db.flush()
                    physical = table("models", column("id", Uuid()), column("tenant_id", Uuid()))
                    for model, tenant in zip(models, [company, other], strict=True):
                        await db.execute(
                            update(physical)
                            .where(physical.c.id == model.id)
                            .values(tenant_id=tenant.id)
                        )
                # Given: actual company-scoped models and a platform system tool.
                path = f"/api/orgs/{organization.id}/catalog-policy"
                original = await client.get(path)
                assert original.status_code == 200
                ids = {row["id"] for row in original.json()["items"]}
                assert str(models[0].id) in ids and str(models[1].id) not in ids
                # When: policy is changed through authenticated HTTP on B11.
                changed = await client.put(
                    path,
                    json={"items": [{"type": "tool", "id": str(tool.id), "enabled": False}]},
                    headers={**login.headers(), "If-Match": original.headers["etag"]},
                )
                # Then: one durable audit and override commit atomically.
                assert changed.status_code == 200
                async with sessions() as db:
                    events = list(
                        (
                            await db.scalars(
                                select(AuditEvent).where(
                                    AuditEvent.action == "org.catalog_policy_update"
                                )
                            )
                        ).all()
                    )
                    assert len(events) == 1
                    persisted = await db.get(Organization, organization.id)
                    assert persisted is not None
                    assert persisted.settings["catalog_policy"] == {
                        "items": [{"type": "tool", "id": str(tool.id), "enabled": False}]
                    }

                replies: list[Response] = []

                async def invite() -> None:
                    replies.append(
                        await client.post(
                            f"/api/orgs/{organization.id}/invitations",
                            json={"email": "concurrent@example.com", "role": "builder"},
                            headers=login.headers(),
                        )
                    )

                # Given: two HTTP retries for the same pending invitation.
                # When: separate PostgreSQL request transactions race.
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(invite)
                    tasks.start_soon(invite)
                # Then: the organization lock prevents duplicate rows and token hashes.
                assert sorted(reply.status_code for reply in replies) == [200, 201]
                assert replies[0].json()["id"] == replies[1].json()["id"]
                assert replies[0].json()["token"] == replies[1].json()["token"]
                async with sessions() as db:
                    rows = list(
                        (
                            await db.scalars(
                                select(OrgInvitation).where(OrgInvitation.org_id == organization.id)
                            )
                        ).all()
                    )
                    assert len(rows) == 1
                    assert rows[0].id == UUID(replies[0].json()["id"])

                # Reissuing the same original link is also serialized across request sessions.
                resent: list[Response] = []

                async def resend() -> None:
                    resent.append(
                        await client.post(
                            f"/api/orgs/{organization.id}/invitations/{replies[0].json()['id']}/resend",
                            headers=login.headers(),
                        )
                    )

                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(resend)
                    tasks.start_soon(resend)
                assert sorted(reply.status_code for reply in resent) == [200, 201]
                assert resent[0].json()["token"] == resent[1].json()["token"]
                recipient = await register_session(
                    client, email="concurrent@example.com", name="Recipient"
                )
                original_denied = await client.post(
                    "/api/invitations/accept",
                    json={"token": replies[0].json()["token"]},
                    headers=recipient.headers(),
                )
                assert original_denied.status_code == 404
                accepted: list[Response] = []

                async def accept() -> None:
                    accepted.append(
                        await client.post(
                            "/api/invitations/accept",
                            json={"token": resent[0].json()["token"]},
                            headers=recipient.headers(),
                        )
                    )

                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(accept)
                    tasks.start_soon(accept)
                assert sorted(reply.status_code for reply in accepted) == [200, 409]
                recipient_id = UUID(recipient.body["user"]["id"])
                async with sessions() as db:
                    member = await db.get(OrganizationMember, (organization.id, recipient_id))
                    assert member is not None and member.status == "active"
                    grants = list(
                        (
                            await db.scalars(
                                select(OrganizationRoleGrant).where(
                                    OrganizationRoleGrant.org_id == organization.id,
                                    OrganizationRoleGrant.principal_id == recipient_id,
                                )
                            )
                        ).all()
                    )
                    assert len(grants) == 1 and grants[0].role == "builder"
                    events = list(
                        (
                            await db.scalars(
                                select(AuditEvent).where(
                                    AuditEvent.action == "org.invitation_accept",
                                    AuditEvent.target_id == resent[0].json()["id"],
                                )
                            )
                        ).all()
                    )
                    assert len(events) == 1
        finally:
            await engine.dispose()
