"""Clone empty head-migrated lab databases from one cached PostgreSQL template.

Upgrading an empty database through the whole Alembic chain costs several seconds, and many
native tests need exactly that state. The first such test in a lane process migrates one
template database; every later one gets ``CREATE DATABASE ... TEMPLATE``. The template lives
in the runner-owned container, so removing the container removes it.

The template name carries a digest of every input that shapes the migrated schema, so a
changed migration can never be served from a stale template. Tests that need a specific
revision, seed rows before upgrading, or run the migration role (I-06) keep real migrations.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL

from alembic import command
from app.config import settings

BACKEND = Path(__file__).resolve().parents[2]
# Every file whose bytes can change what ``alembic upgrade head`` builds.
SCHEMA_INPUTS = (
    BACKEND / "alembic" / "versions",
    BACKEND / "alembic" / "env.py",
    BACKEND / "app" / "migrations",
)

_lock = threading.Lock()
_ready: set[str] = set()


def schema_inputs_digest(paths: tuple[Path, ...], root: Path = BACKEND) -> str:
    """Hash the ``root``-relative path and bytes of every Python file under ``paths``."""
    digest = hashlib.sha256()
    for root in paths:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def alembic_config(url: URL) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    return config


def template_name(head: str, digest: str, *, rls_enabled: bool) -> str:
    """Key the template by head revision, schema-input digest and the RLS migration switch."""
    key = hashlib.sha256(f"{head}\0{digest}\0{rls_enabled}".encode()).hexdigest()
    return "moldy_pg_lane_tpl_" + key[:32]


def _head(config: Config) -> str:
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


def _upgrade_head(config: Config) -> None:
    # env.py drives asyncio.run; keep it off any running event loop.
    with ThreadPoolExecutor(1) as pool:
        pool.submit(command.upgrade, config, "head").result()


def _quote(name: str) -> str:
    assert name.replace("_", "").isalnum(), name
    return f'"{name}"'


def _terminate(connection: sa.Connection, database: str) -> None:
    connection.execute(
        sa.text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :name AND pid <> pg_backend_pid()"
        ),
        {"name": database},
    )


def _build_template(admin: sa.Engine, source: URL, template: str, head: str) -> None:
    build = template + "_build"
    with admin.connect() as connection:
        # A half-built leftover (interrupted earlier build) is never reused.
        for stale in (build, template):
            connection.execute(sa.text(f"DROP DATABASE IF EXISTS {_quote(stale)} WITH (FORCE)"))
        connection.execute(sa.text(f"CREATE DATABASE {_quote(build)}"))
    try:
        target = source.set(database=build)
        _upgrade_head(alembic_config(target))
        probe = sa.create_engine(target, poolclass=sa.NullPool)
        try:
            with probe.connect() as connection:
                current = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        finally:
            probe.dispose()
        assert current == head, (current, head)
        with admin.connect() as connection:
            # A template must have no other sessions: close anything Alembic left behind,
            # then refuse new connections so no later test can hold the template open.
            _terminate(connection, build)
            connection.execute(
                sa.text(f"ALTER DATABASE {_quote(build)} RENAME TO {_quote(template)}")
            )
            connection.execute(
                sa.text(
                    f"ALTER DATABASE {_quote(template)} IS_TEMPLATE true ALLOW_CONNECTIONS false"
                )
            )
    except BaseException:
        with admin.connect() as connection:
            connection.execute(sa.text(f"DROP DATABASE IF EXISTS {_quote(build)} WITH (FORCE)"))
        raise


def _ensure_template(admin: sa.Engine, source: URL) -> str:
    rls_enabled = bool(settings.db_rls_enabled)
    config = alembic_config(source)
    head = _head(config)
    digest = schema_inputs_digest(SCHEMA_INPUTS)
    template = template_name(head, digest, rls_enabled=rls_enabled)
    with _lock:
        if template in _ready:
            return template
        with admin.connect() as connection:
            ready = connection.scalar(
                sa.text("SELECT datistemplate FROM pg_database WHERE datname = :name"),
                {"name": template},
            )
        if ready is not True:
            _build_template(admin, source, template, head)
        _ready.add(template)
        return template


def create_head_database(source: URL, database: str) -> None:
    """Create ``database`` as an empty database already at the Alembic head."""
    # A separate app role changes what the chain grants (I-06), so it is never templated.
    assert not settings.migration_database_url, "migration-role labs must run real migrations"
    admin = sa.create_engine(source, isolation_level="AUTOCOMMIT", poolclass=sa.NullPool)
    try:
        template = _ensure_template(admin, source)
        with admin.connect() as connection:
            connection.execute(
                sa.text(f"CREATE DATABASE {_quote(database)} TEMPLATE {_quote(template)}")
            )
    finally:
        admin.dispose()
