from uuid import uuid5

import pytest
import sqlalchemy as sa

from app.migrations.org_authz_v1.contracts import ORG
from app.migrations.org_authz_v1.m82_backfill import backfill, reverse_backfill
from app.migrations.org_authz_v1.metadata import reflect_metadata
from tests.authz.org_authz_b02_fixture import IDS, prepare, seed


@pytest.mark.parametrize("explicit", [True, False])
def test_owner_precedence_system_resources_and_idempotent_grants(explicit: bool) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        legacy = prepare(connection)
        expected = seed(connection, legacy)
        conversations = legacy.tables["conversations"]
        if explicit:
            connection.execute(
                conversations.update()
                .where(conversations.c.id == IDS["api"])
                .values(user_id=IDS["builder"])
            )
            expected[IDS["api"]] = IDS["builder"]
            expected[IDS["side"]] = IDS["builder"]
        backfill(connection)
        backfill(connection)
        reflected = reflect_metadata(connection)
        conversations = reflected.tables["conversations"]
        assert (
            dict(
                connection.execute(sa.select(conversations.c.id, conversations.c.user_id))
                .tuples()
                .all()
            )
            == expected
        )
        credentials = reflected.tables["credentials"]
        assert tuple(
            connection.execute(
                sa.select(credentials.c.scope, credentials.c.org_id, credentials.c.tenant_id)
            ).one()
        ) == ("platform", None, None)
        grants = reflected.tables["organization_role_grants"]
        assert set(connection.scalars(sa.select(grants.c.id))) == {
            uuid5(ORG, f"legacy/{IDS[key]}/{role}")
            for key, role in (("admin", "admin"), ("api_user", "builder"), ("builder", "builder"))
        }
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(reflected.tables["tenant_members"])
            )
            == 3
        )
    engine.dispose()


def test_missing_hidden_owner_rolls_back_all_prior_upgrade_writes() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        legacy = prepare(connection)
        seed(connection, legacy)
        connection.execute(legacy.tables["skill_builder_sessions"].delete())
        with (
            pytest.raises(ValueError, match="Cannot resolve conversation owners"),
            connection.begin_nested(),
        ):
            backfill(connection)
        metadata = reflect_metadata(connection)
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(metadata.tables["tenants"]))
            == 0
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(metadata.tables["tenant_members"])
            )
            == 0
        )
        assert set(connection.scalars(sa.select(metadata.tables["conversations"].c.user_id))) == {
            None
        }
    engine.dispose()


def test_preserved_preference_and_timestamps_survive_round_trip() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        legacy = prepare(connection)
        seed(connection, legacy)
        users = legacy.tables["users"]
        # A preference for a different identity is historical data even with FK checks disabled.
        preference = IDS["deployment"]
        connection.execute(
            users.update().where(users.c.id == IDS["admin"]).values(last_active_org_id=preference)
        )
        before = connection.execute(
            sa.select(users.c.id, users.c.last_active_org_id, users.c.created_at).order_by(
                users.c.id
            )
        ).all()
        backfill(connection)
        reverse_backfill(connection)
        after = connection.execute(
            sa.select(users.c.id, users.c.last_active_org_id, users.c.created_at).order_by(
                users.c.id
            )
        ).all()
        assert before == after
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(legacy.tables["tenants"])) == 0
        )
    engine.dispose()


@pytest.mark.parametrize("source", ["agent_api_threads", "skill_builder_sessions"])
def test_conflicting_owner_sources_fail_closed(source: str) -> None:
    from app.migrations.org_authz_v1.conversation_owners import unique_owners

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    table = sa.Table(
        source,
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid()),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            table.insert(),
            [
                {"id": 1, "conversation_id": IDS["main"], "user_id": IDS["admin"]},
                {"id": 2, "conversation_id": IDS["main"], "user_id": IDS["builder"]},
            ],
        )
        with pytest.raises(ValueError, match="Ambiguous conversation owner"):
            unique_owners(connection, table)
    engine.dispose()


def test_preexisting_scope_is_preserved_and_missing_agent_owner_fails() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        legacy = prepare(connection)
        seed(connection, legacy)
        agents = legacy.tables["agents"]
        connection.execute(
            agents.update()
            .where(agents.c.id == IDS["agent"])
            .values(org_id=IDS["deployment"], tenant_id=IDS["deployment"])
        )
        backfill(connection)
        reflected = reflect_metadata(connection)
        assert (
            connection.scalar(
                sa.select(reflected.tables["agents"].c.org_id).where(
                    reflected.tables["agents"].c.id == IDS["agent"]
                )
            )
            == IDS["deployment"]
        )
        reverse_backfill(connection)
        connection.execute(agents.delete().where(agents.c.id == IDS["agent"]))
        with (
            pytest.raises(ValueError, match="Cannot resolve conversation owners"),
            connection.begin_nested(),
        ):
            backfill(connection)
    engine.dispose()
