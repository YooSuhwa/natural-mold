"""RLS must cover physical m77 history as well as the draft schema."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy.engine import Connection, Engine, make_url

from alembic import command
from schema_drafts.org_authz.chain import DRAFTS
from schema_drafts.org_authz.default_org_backfill import TENANT
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.backfill_fixture import seed
from tests.authz.test_full_draft_chain import execute_draft

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RlsLab:
    engine: Engine
    role: str


@pytest.fixture(scope="module")
def rls_lab() -> Iterator[RlsLab]:
    source = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    database = "moldy_pg_lane_rls_" + uuid4().hex
    role = "rls_actor_" + uuid4().hex
    admin = sa.create_engine(source, isolation_level="AUTOCOMMIT")
    target = source.set(database=database)
    engine = sa.create_engine(target)
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
            created = True
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        config.attributes["database_url"] = target.set(
            drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False)
        command.upgrade(config, "head")
        # Actual saver setup owns four public tables outside the Alembic chain.
        with PostgresSaver.from_conn_string(
            target.set(drivername="postgresql").render_as_string(hide_password=False)
        ) as saver:
            saver.setup()
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {"private": {"payload": "fixture-only"}}
            checkpoint["channel_versions"] = {"private": "1"}
            saved = saver.put(
                {"configurable": {"thread_id": "private-thread", "checkpoint_ns": ""}},
                checkpoint,
                {"source": "input", "step": -1},
                {"private": "1"},
            )
            saver.put_writes(saved, [("private", {"payload": "fixture-only"})], "private-task")
        with engine.begin() as connection:
            for draft in DRAFTS[:4]:
                execute_draft(connection, draft)
            seed(connection, schema_metadata(scope_columns=True))
            for draft in DRAFTS[4:13]:
                execute_draft(connection, draft, disposable=True)
            connection.execute(sa.text(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS'))
            connection.execute(sa.text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            connection.execute(
                sa.text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f'IN SCHEMA public TO "{role}"'
                )
            )
        yield RlsLab(engine, role)
    finally:
        engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(sa.text(f'DROP DATABASE "{database}" WITH (FORCE)'))
                connection.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


def tenant_actor(connection: Connection, lab: RlsLab, tenant: UUID = TENANT) -> None:
    connection.execute(sa.text(f'SET LOCAL ROLE "{lab.role}"'))
    connection.execute(
        sa.text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant)}
    )


def test_every_physical_table_has_one_forced_policy_when_m92_is_installed(rls_lab: RlsLab) -> None:
    # Given: actual m77 migrations and all drafts through m92 have run.
    with rls_lab.engine.begin() as connection:
        expected = set(sa.inspect(connection).get_table_names())
        # When: PostgreSQL reports installed row-security and policy inventory.
        rows = connection.execute(
            sa.text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "count(p.oid) AS policies FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "LEFT JOIN pg_policy p ON p.polrelid=c.oid "
                "WHERE n.nspname='public' AND c.relkind='r' "
                "GROUP BY c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity"
            )
        ).all()
        protected = {
            name for name, enabled, forced, count in rows if enabled and forced and count == 1
        }
        # Then: migration metadata, retained historical tables and every app table are classified.
        assert protected == expected


def test_unclassified_table_aborts_before_policy_installation(rls_lab: RlsLab) -> None:
    # Given: an app developer introduced a physical payload table without reviewing its scope.
    with rls_lab.engine.begin() as connection, connection.begin_nested() as transaction:
        connection.execute(sa.text("CREATE TABLE unclassified_payload (id uuid PRIMARY KEY)"))
        # When: m92 is requested against the changed schema.
        with pytest.raises(RuntimeError, match="unclassified_payload"):
            execute_draft(connection, DRAFTS[12], disposable=True)
        # Then: the classification guard refuses the schema before executing any policy DDL.
        transaction.rollback()


def test_identity_lookup_requires_deliberate_service_scope(rls_lab: RlsLab) -> None:
    # Given: a non-bypass app role has neither tenant nor authenticated-user context.
    with rls_lab.engine.begin() as connection:
        connection.execute(sa.text(f'SET LOCAL ROLE "{rls_lab.role}"'))
        assert connection.scalar(sa.text("SELECT count(*) FROM users")) == 0
        # When: the trusted login gateway supplies its explicit identity scope.
        connection.execute(sa.text("SELECT set_config('app.scope', 'identity', true)"))
        # Then: pre-tenant login lookup works, without opening tenant payload tables.
        assert connection.scalar(sa.text("SELECT count(*) FROM users")) == 3
        assert connection.scalar(sa.text("SELECT count(*) FROM conversations")) == 0


def test_platform_history_is_hidden_from_tenant_catalog_readers(rls_lab: RlsLab) -> None:
    # Given: a platform credential is readable as catalog data, with a private audit record.
    metadata = schema_metadata(later_columns=True)
    audits = metadata.tables["credential_audit_logs"]
    with rls_lab.engine.begin() as connection:
        credential = connection.scalar(
            sa.text("SELECT id FROM credentials WHERE tenant_id IS NULL")
        )
        audit = uuid4()
        connection.execute(
            sa.insert(audits).values(id=audit, credential_id=credential, action="read")
        )
        tenant_actor(connection, rls_lab)
        # When: a tenant queries the audit row directly without a parent authorization join.
        visible = connection.scalar(
            sa.select(sa.func.count()).select_from(audits).where(audits.c.id == audit)
        )
        # Then: public catalog visibility cannot disclose platform credential history.
        assert visible == 0


@pytest.mark.parametrize(
    "name", ["checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"]
)
def test_runtime_checkpoint_payload_requires_trusted_service_context(
    rls_lab: RlsLab, name: str
) -> None:
    # Given: the actual PostgresSaver wrote graph payloads and initialized its migrations.
    with rls_lab.engine.begin() as connection:
        table = sa.Table(name, sa.MetaData(), autoload_with=connection)
        tenant_actor(connection, rls_lab)
        # When: ordinary tenant SQL requests runtime-managed graph state directly.
        visible = connection.scalar(sa.select(sa.func.count()).select_from(table))
        # Then: neither the tenant ID nor a public thread identifier discloses graph data.
        assert visible == 0
        connection.execute(sa.text("SELECT set_config('app.scope', 'platform', true)"))
        assert connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() > 0
