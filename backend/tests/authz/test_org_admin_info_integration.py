"""A locked PostgreSQL organization version prevents concurrent policy overwrites."""

from collections.abc import AsyncGenerator

import anyio
import pytest
from anyio.to_thread import run_sync
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.dependencies import get_db
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
)
from app.models.user import User
from app.schemas.auth import UserResponse
from app.services import audit_service
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.organization_migration_fixture import async_organization_lab
from tests.conftest import register_session

pytestmark = pytest.mark.integration


async def test_live_b11_metadata_policy_uses_real_session_and_locked_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with async_organization_lab() as lab:
        await run_sync(command.upgrade, lab.config, "head")
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
                    client, email="pg-org-admin@example.com", name="PG org admin"
                )
                user_id = UserResponse.model_validate(login.body["user"]).id
                async with sessions() as db, db.begin():
                    tenant = Tenant(name="Policy", slug="policy", secrets_namespace="policy")
                    db.add(tenant)
                    await db.flush()
                    organization = Organization(
                        tenant_id=tenant.id,
                        name="Policy",
                        slug="policy",
                        settings={"opaque_existing_setting": {"fixture": "preserve"}},
                    )
                    db.add(organization)
                    await db.flush()
                    db.add(TenantMember(tenant_id=tenant.id, user_id=user_id))
                    db.add(
                        OrganizationMember(
                            org_id=organization.id, tenant_id=tenant.id, user_id=user_id
                        )
                    )
                    db.add(
                        OrganizationRoleGrant(
                            org_id=organization.id,
                            tenant_id=tenant.id,
                            role="admin",
                            principal_type="user",
                            principal_id=user_id,
                        )
                    )
                # Given: actual B11 schema, real JWT/CSRF cookies and the same original version.
                original = await client.get(f"/api/orgs/{organization.id}")
                assert original.status_code == 200
                assert "opaque_existing_setting" not in original.json()["settings"]
                headers = {**login.headers(), "If-Match": original.headers["etag"]}
                # When: two separate request transactions race with that version.
                responses: list[Response] = []

                async def change_name(name: str) -> None:
                    responses.append(
                        await client.patch(
                            f"/api/orgs/{organization.id}", json={"name": name}, headers=headers
                        )
                    )

                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(change_name, "First policy")
                    tasks.start_soon(change_name, "Second policy")
                assert sorted(response.status_code for response in responses) == [200, 409]
                failed = next(response for response in responses if response.status_code == 409)
                assert failed.json()["error"]["code"] == "stale_version"
                successful = next(response for response in responses if response.status_code == 200)
                # Then: only the locked winner commits; hidden settings and one audit persist.
                async with sessions() as db:
                    persisted = await db.get(Organization, organization.id)
                    assert persisted is not None
                    assert persisted.name == successful.json()["name"]
                    assert persisted.settings == {
                        "opaque_existing_setting": {"fixture": "preserve"}
                    }
                    assert persisted.authz_epoch == 1
                    events = list(
                        (
                            await db.scalars(
                                select(AuditEvent).where(
                                    AuditEvent.action == "org.update",
                                    AuditEvent.target_id == str(organization.id),
                                )
                            )
                        ).all()
                    )
                    assert len(events) == 1
                    assert events[0].actor_user_id == user_id

                # The same real schema supports manual groups and tagged assignment deltas.
                headers["If-Match"] = successful.headers["etag"]
                created = await client.post(
                    f"/api/orgs/{organization.id}/groups",
                    json={"name": "PG directory"},
                    headers=headers,
                )
                assert created.status_code == 201
                group_id = created.json()["id"]
                headers["If-Match"] = created.headers["etag"]
                member = await client.post(
                    f"/api/orgs/{organization.id}/groups/{group_id}/members",
                    json={"user_id": str(user_id)},
                    headers=headers,
                )
                assert member.status_code == 201
                headers["If-Match"] = member.headers["etag"]
                assigned = await client.put(
                    f"/api/orgs/{organization.id}/roles",
                    json={"principal": {"type": "group", "id": group_id}, "roles": ["auditor"]},
                    headers=headers,
                )
                assert assigned.status_code == 200
                headers["If-Match"] = assigned.headers["etag"]
                capabilities = await client.put(
                    f"/api/orgs/{organization.id}/capabilities",
                    json={
                        "principal": {"type": "organization", "id": str(organization.id)},
                        "capabilities": ["cap_create_skill"],
                    },
                    headers=headers,
                )
                assert capabilities.status_code == 200
                roster = await client.get(f"/api/orgs/{organization.id}/groups/{group_id}/members")
                assert roster.status_code == 200
                assert roster.json()["items"][0]["user_id"] == str(user_id)

                async with sessions() as db, db.begin():
                    peer = User(email="pg-member-exit@example.com", name="Exit candidate")
                    db.add(peer)
                    await db.flush()
                    db.add(TenantMember(tenant_id=tenant.id, user_id=peer.id))
                headers["If-Match"] = capabilities.headers["etag"]
                joined = await client.post(
                    f"/api/orgs/{organization.id}/members",
                    json={"user_id": str(peer.id)},
                    headers=headers,
                )
                assert joined.status_code == 201
                headers["If-Match"] = joined.headers["etag"]
                # A private B11 resource must be transferred before its member can leave.
                source = schema_metadata(scope_columns=True)
                async with sessions() as db, db.begin():
                    model_id = (
                        await db.execute(
                            source.tables["models"]
                            .insert()
                            .values(
                                provider="openai",
                                model_name="exit-fixture",
                                display_name="Exit fixture",
                            )
                            .returning(source.tables["models"].c.id)
                        )
                    ).scalar_one()
                    owned_id = (
                        await db.execute(
                            source.tables["agents"]
                            .insert()
                            .values(
                                user_id=peer.id,
                                name="Owned fixture",
                                system_prompt="Fixture",
                                model_id=model_id,
                                runtime_name="owned_exit_fixture",
                                runtime_profile="standard",
                                org_id=organization.id,
                                tenant_id=tenant.id,
                            )
                            .returning(source.tables["agents"].c.id)
                        )
                    ).scalar_one()
                    trigger = source.tables["agent_triggers"]
                    trigger_id = (
                        await db.execute(
                            trigger.insert()
                            .values(
                                agent_id=owned_id,
                                user_id=peer.id,
                                name="Pause fixture",
                                trigger_type="interval",
                                schedule_config={"seconds": 3600},
                                input_message="Fixture",
                                org_id=organization.id,
                                tenant_id=tenant.id,
                            )
                            .returning(trigger.c.id)
                        )
                    ).scalar_one()
                suspended = await client.patch(
                    f"/api/orgs/{organization.id}/members/{peer.id}",
                    json={"status": "suspended"},
                    headers=headers,
                )
                assert suspended.status_code == 200
                async with sessions() as db:
                    assert (
                        await db.scalar(select(trigger.c.status).where(trigger.c.id == trigger_id))
                        == "paused"
                    )
                headers["If-Match"] = suspended.headers["etag"]
                resumed = await client.patch(
                    f"/api/orgs/{organization.id}/members/{peer.id}",
                    json={"status": "active"},
                    headers=headers,
                )
                assert resumed.status_code == 200
                headers["If-Match"] = resumed.headers["etag"]
                refused = await client.delete(
                    f"/api/orgs/{organization.id}/members/{peer.id}", headers=headers
                )
                assert refused.status_code == 409
                assert refused.json()["error"]["code"] == "resource_transfer_required"
                async with sessions() as db, db.begin():
                    member_row = await db.get(OrganizationMember, (organization.id, peer.id))
                    assert member_row is not None and member_row.status == "active"
                    await db.execute(
                        source.tables["agents"]
                        .delete()
                        .where(source.tables["agents"].c.id == owned_id)
                    )
                departed = await client.delete(
                    f"/api/orgs/{organization.id}/members/{peer.id}", headers=headers
                )
                assert departed.status_code == 204
                async with sessions() as db:
                    member_row = await db.get(OrganizationMember, (organization.id, peer.id))
                    assert member_row is not None and member_row.status == "removed"
                    assert await db.get(User, peer.id) is not None
        finally:
            await engine.dispose()
