"""Member exit cannot stop another organization's schedules or revoke its API keys."""

import pytest
from anyio.to_thread import run_sync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.authz.org_control import OrgControlContext
from app.authz.org_member_exit import (
    pause_member_triggers,
    require_no_owned_resources,
    stop_member_entry_points,
)
from app.dependencies import CurrentUser
from app.models.organization import Organization, Tenant
from app.models.user import User
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.organization_migration_fixture import async_organization_lab

pytestmark = pytest.mark.integration


async def test_exit_entry_points_only_affect_the_selected_organization() -> None:
    async with async_organization_lab() as lab:
        await run_sync(command.upgrade, lab.config, "head")
        engine = create_async_engine(lab.engine.url.set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db, db.begin():
                company = Tenant(name="Exit", slug="exit", secrets_namespace="exit")
                user = User(email="pg-scope-exit@example.com", name="Exit")
                other_owner = User(email="pg-other-owner@example.com", name="Other owner")
                db.add_all([company, user, other_owner])
                await db.flush()
                organizations = [
                    Organization(tenant_id=company.id, name=name, slug=name)
                    for name in ("one", "two")
                ]
                db.add_all(organizations)
                await db.flush()
                source = schema_metadata(scope_columns=True)
                model_id = (
                    await db.execute(
                        source.tables["models"]
                        .insert()
                        .values(provider="openai", model_name="fixture", display_name="Fixture")
                        .returning(source.tables["models"].c.id)
                    )
                ).scalar_one()
                foreign_agent = (
                    await db.execute(
                        source.tables["agents"]
                        .insert()
                        .values(
                            user_id=user.id,
                            name="Other organization's resource",
                            model_id=model_id,
                            system_prompt="Fixture",
                            runtime_name="foreign_fixture",
                            runtime_profile="standard",
                            org_id=organizations[1].id,
                            tenant_id=company.id,
                        )
                        .returning(source.tables["agents"].c.id)
                    )
                ).scalar_one()
                local_agent = (
                    await db.execute(
                        source.tables["agents"]
                        .insert()
                        .values(
                            user_id=other_owner.id,
                            name="Local resource",
                            model_id=model_id,
                            system_prompt="Fixture",
                            runtime_name="local_fixture",
                            runtime_profile="standard",
                            org_id=organizations[0].id,
                            tenant_id=company.id,
                        )
                        .returning(source.tables["agents"].c.id)
                    )
                ).scalar_one()
                triggers, keys = source.tables["agent_triggers"], source.tables["agent_api_keys"]
                trigger_ids = []
                key_ids = []
                for org, agent_id in zip(organizations, [local_agent, foreign_agent], strict=True):
                    trigger_ids.append(
                        (
                            await db.execute(
                                triggers.insert()
                                .values(
                                    user_id=user.id,
                                    agent_id=agent_id,
                                    name="Fixture",
                                    trigger_type="interval",
                                    schedule_config={"seconds": 3600},
                                    input_message="Fixture",
                                    org_id=org.id,
                                    tenant_id=company.id,
                                )
                                .returning(triggers.c.id)
                            )
                        ).scalar_one()
                    )
                    key_ids.append(
                        (
                            await db.execute(
                                keys.insert()
                                .values(
                                    user_id=user.id,
                                    name="Fixture",
                                    key_id=org.slug,
                                    key_hash="0" * 64,
                                    prefix="fixture",
                                    last_four="0000",
                                    org_id=org.id,
                                    tenant_id=company.id,
                                )
                                .returning(keys.c.id)
                            )
                        ).scalar_one()
                    )
                context = OrgControlContext(
                    organization=organizations[0],
                    actor=CurrentUser(
                        id=user.id, email=user.email, name=user.name, is_super_user=False
                    ),
                    permissions=("can_manage_members",),
                )
                # Given: the departing user's resource is owned only in the other org.
                await require_no_owned_resources(db, context, user.id)
                # When: suspension pauses the selected org's entry points.
                await pause_member_triggers(db, context, user.id)
                # Then: another org's trigger remains active and neither key is revoked.
                statuses = [
                    await db.scalar(select(triggers.c.status).where(triggers.c.id == key))
                    for key in trigger_ids
                ]
                assert statuses == ["paused", "active"]
                revoked = [
                    await db.scalar(select(keys.c.revoked_at).where(keys.c.id == key)) is None
                    for key in key_ids
                ]
                assert all(revoked)
                # When: permanent exit revokes the selected org's key.
                await stop_member_entry_points(db, context, user.id)
                # Then: the other key remains valid.
                assert (
                    await db.scalar(select(keys.c.revoked_at).where(keys.c.id == key_ids[0]))
                    is not None
                )
                assert (
                    await db.scalar(select(keys.c.revoked_at).where(keys.c.id == key_ids[1]))
                    is None
                )
        finally:
            await engine.dispose()
