"""M9 migration PostgreSQL round-trip integration test.

Default pytest run skips this (`addopts = -m 'not integration'`). To execute:

    cd backend
    docker-compose up -d postgres
    INTEGRATION_DATABASE_URL='postgresql+psycopg://moldy:moldy@localhost:5432/moldy' \
      uv run pytest -m integration tests/integration/test_m9_pg_roundtrip.py

The configured PostgreSQL role must have `CREATEDB`: the fixture creates a
UUID-named temporary database for the migration round-trip and drops it during
teardown.

The m9 upgrade uses `CAST(:extra_config AS JSON)` and `TRUE` literals that are
PostgreSQL-specific, so we cannot verify the migration on the shared aiosqlite
engine used by the regular suite. The assertions below:

1. Seed a representative `mcp_servers` row (with `auth_config` plaintext)
2. `alembic upgrade m9` → ensure a `connections` row is created, the tool
   FK pivoted to `connection_id`, and the migration is recorded in the
   `_m9_migrated_connections` tracking table
3. Insert an extra user-created `type='mcp'` connection (not tracked)
4. `alembic downgrade -1` → ensure only the tracked row is removed, the
   user-created one survives, and the tracking table is dropped
5. `alembic upgrade m9` again → idempotent, tracking row re-created

The migration sequence runs in a fresh temporary database. This prevents the
test from downgrading a shared database from the current head through newer,
intentionally irreversible migrations.

Rationale (Codex adversarial Finding 4, code-reviewer follow-up #4): M6 cutoff
will rely on this being correct. The unit tests in
`test_connection_mcp_resolve.py` cover the helper contracts in isolation but
do not prove PG-level JSON semantics.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import URL, Engine, make_url

from alembic import command

pytestmark = pytest.mark.integration


PG_DSN_ENV = "INTEGRATION_DATABASE_URL"


@pytest.fixture
def isolated_database_urls() -> Iterator[tuple[URL, URL]]:
    raw_dsn = os.environ.get(PG_DSN_ENV)
    if not raw_dsn:
        pytest.skip(f"{PG_DSN_ENV} not set — point at a disposable Postgres for this test")

    base_url = make_url(raw_dsn)
    if base_url.drivername not in {
        "postgresql",
        "postgresql+asyncpg",
        "postgresql+psycopg",
    }:
        pytest.fail(f"{PG_DSN_ENV} must use a PostgreSQL URL")

    sync_base_url = base_url.set(drivername="postgresql+psycopg")
    database_name = f"moldy_m9_{uuid.uuid4().hex[:12]}"
    admin_url = sync_base_url.set(database="postgres")
    admin_engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    quoted_database_name = admin_engine.dialect.identifier_preparer.quote(database_name)

    try:
        with admin_engine.connect() as conn:
            conn.execute(sa.text(f"CREATE DATABASE {quoted_database_name}"))

        sync_test_url = sync_base_url.set(database=database_name)
        async_test_url = sync_test_url.set(drivername="postgresql+asyncpg")
        try:
            yield sync_test_url, async_test_url
        finally:
            with admin_engine.connect() as conn:
                conn.execute(
                    sa.text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                conn.execute(sa.text(f"DROP DATABASE {quoted_database_name}"))
    finally:
        admin_engine.dispose()


@pytest.fixture
def pg_engine(isolated_database_urls: tuple[URL, URL]) -> Iterator[Engine]:
    sync_url, _ = isolated_database_urls
    engine = sa.create_engine(sync_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def alembic_config(isolated_database_urls: tuple[URL, URL]) -> Config:
    backend_root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    _, async_url = isolated_database_urls
    cfg.attributes["database_url"] = async_url.render_as_string(hide_password=False)
    return cfg


def test_m9_roundtrip_preserves_user_created_mcp_connections(
    pg_engine: Engine,
    alembic_config: Config,
) -> None:
    # Build an isolated m8 baseline from an empty database.
    command.upgrade(alembic_config, "m8_add_connections")

    # Seed an mcp_server row (+ its corresponding tools row) to be migrated
    user_id = uuid.uuid4()
    server_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, name, created_at) VALUES (:id, :email, :name, now())"
            ),
            {"id": user_id, "email": f"m9-{user_id.hex[:8]}@t", "name": "u"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO mcp_servers "
                "(id, user_id, name, url, auth_type, auth_config, "
                " status, created_at) "
                "VALUES (:id, :uid, :name, :url, :at, "
                " CAST(:ac AS JSON), 'active', now())"
            ),
            {
                "id": server_id,
                "uid": user_id,
                "name": "Resend MCP",
                "url": "https://resend.example.com/mcp",
                "at": "api_key",
                "ac": '{"RESEND_API_KEY": "sk-plaintext"}',
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO tools "
                "(id, user_id, type, is_system, mcp_server_id, name, "
                " created_at) "
                "VALUES (:id, :uid, 'mcp', false, :sid, :name, now())"
            ),
            {
                "id": tool_id,
                "uid": user_id,
                "sid": server_id,
                "name": "resend_send",
            },
        )

    # Apply m9
    command.upgrade(alembic_config, "m9_migrate_mcp_to_connections")

    # Migrated row exists; tool FK pivoted; tracking table recorded
    with pg_engine.connect() as conn:
        migrated = conn.execute(
            sa.text(
                "SELECT id, provider_name, extra_config "
                "FROM connections WHERE user_id = :uid AND type = 'mcp'"
            ),
            {"uid": user_id},
        ).fetchone()
        assert migrated is not None
        # Critical: extra_config matches ConnectionExtraConfig (extra='forbid')
        assert set(migrated[2].keys()) == {
            "url",
            "auth_type",
            "headers",
            "env_vars",
        }

        tracked = conn.execute(
            sa.text(
                "SELECT connection_id FROM _m9_migrated_connections WHERE connection_id = :cid"
            ),
            {"cid": migrated[0]},
        ).scalar()
        assert tracked == migrated[0]

        pivoted = conn.execute(
            sa.text("SELECT connection_id FROM tools WHERE id = :tid"),
            {"tid": tool_id},
        ).scalar()
        assert pivoted == migrated[0]

    # Simulate a user manually creating another mcp-type connection
    user_conn_id = uuid.uuid4()
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO connections "
                "(id, user_id, type, provider_name, display_name, "
                " extra_config, is_default, status, created_at, updated_at) "
                "VALUES (:id, :uid, 'mcp', :pn, :dn, "
                " CAST(:ec AS JSON), false, 'active', now(), now())"
            ),
            {
                "id": user_conn_id,
                "uid": user_id,
                "pn": "my_custom_mcp",
                "dn": "My Custom MCP",
                "ec": '{"url": "https://x", "auth_type": "none"}',
            },
        )

    # Downgrade → only the sentinel row should disappear
    command.downgrade(alembic_config, "m8_add_connections")

    with pg_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT id FROM connections WHERE user_id = :uid AND type = 'mcp'"),
            {"uid": user_id},
        ).fetchall()
        ids = {row[0] for row in rows}
        assert user_conn_id in ids, "user-created MCP connection was wiped"
        # migrated one is gone
        assert len(ids) == 1

    # Re-upgrade to verify the migration remains idempotent. The isolated
    # database fixture owns cleanup and drops the whole database afterward.
    command.upgrade(alembic_config, "m9_migrate_mcp_to_connections")

    with pg_engine.connect() as conn:
        re_tracked = conn.execute(
            sa.text(
                "SELECT connection_id FROM _m9_migrated_connections "
                "WHERE connection_id IN ("
                "SELECT id FROM connections WHERE user_id = :uid AND type = 'mcp'"
                ")"
            ),
            {"uid": user_id},
        ).scalar()
        assert re_tracked is not None

        rows = conn.execute(
            sa.text("SELECT id FROM connections WHERE user_id = :uid AND type = 'mcp'"),
            {"uid": user_id},
        ).fetchall()
        assert {row[0] for row in rows} == {user_conn_id, re_tracked}
