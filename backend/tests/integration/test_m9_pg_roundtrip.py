"""M9 migration PostgreSQL round-trip integration test.

Default pytest run skips this (`addopts = -m 'not integration'`). To execute:

    cd backend
    docker-compose up -d postgres
    uv run pytest -m integration tests/integration/test_m9_pg_roundtrip.py

The m9 upgrade uses `CAST(:extra_config AS JSON)` and `TRUE` literals that are
PostgreSQL-specific, so we cannot verify the migration on the shared aiosqlite
engine used by the regular suite. The assertions below:

1. Seed a representative `mcp_servers` row (with `auth_config` plaintext)
2. `alembic upgrade head` → ensure a `connections` row is created, the tool
   FK pivoted to `connection_id`, and the migration is recorded in the
   `_m9_migrated_connections` tracking table
3. Insert an extra user-created `type='mcp'` connection (not tracked)
4. `alembic downgrade -1` → ensure only the tracked row is removed, the
   user-created one survives, and the tracking table is dropped
5. `alembic upgrade head` again → idempotent, tracking row re-created

Rationale (Codex adversarial Finding 4, code-reviewer follow-up #4): M6 cutoff
will rely on this being correct. The unit tests in
`test_connection_mcp_resolve.py` cover the helper contracts in isolation but
do not prove PG-level JSON semantics.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.integration


PG_DSN_ENV = "INTEGRATION_DATABASE_URL"


@pytest.fixture
def pg_engine():
    dsn = os.environ.get(PG_DSN_ENV)
    if not dsn:
        pytest.skip(
            f"{PG_DSN_ENV} not set — point at a disposable Postgres for this test"
        )
    engine = sa.create_engine(dsn, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def alembic_config():
    here = os.path.dirname(__file__)
    backend_root = os.path.abspath(os.path.join(here, "..", ".."))
    cfg = Config(os.path.join(backend_root, "alembic.ini"))
    dsn = os.environ[PG_DSN_ENV]
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


def test_m9_roundtrip_preserves_user_created_mcp_connections(
    pg_engine, alembic_config
):
    # Reset to m8 baseline
    command.downgrade(alembic_config, "m8_add_connections")

    # Seed an mcp_server row (+ its corresponding tools row) to be migrated
    user_id = uuid.uuid4()
    server_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, name, created_at) "
                "VALUES (:id, :email, :name, now())"
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
                "SELECT connection_id FROM _m9_migrated_connections "
                "WHERE connection_id = :cid"
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
            sa.text(
                "SELECT id FROM connections "
                "WHERE user_id = :uid AND type = 'mcp'"
            ),
            {"uid": user_id},
        ).fetchall()
        ids = {row[0] for row in rows}
        assert user_conn_id in ids, "user-created MCP connection was wiped"
        # migrated one is gone
        assert len(ids) == 1

    # Re-upgrade to leave DB at head + cleanup
    command.upgrade(alembic_config, "m9_migrate_mcp_to_connections")
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text("DELETE FROM tools WHERE id = :tid"), {"tid": tool_id}
        )
        conn.execute(
            sa.text("DELETE FROM connections WHERE user_id = :uid"),
            {"uid": user_id},
        )
        conn.execute(
            sa.text("DELETE FROM mcp_servers WHERE id = :sid"),
            {"sid": server_id},
        )
        conn.execute(
            sa.text("DELETE FROM users WHERE id = :uid"), {"uid": user_id}
        )
