"""m92 (I-06): app/migration DB role separation and RLS activation.

Roles (plan 8.8, 16 ``DB_RLS_ENABLED`` row; runbook ``docs/operator-setup.md``
section 8): ``MIGRATION_DATABASE_URL`` connects as the migration role, which
owns every table and must be ``BYPASSRLS`` (or a superuser) so later data
migrations still see every company. ``DATABASE_URL`` connects as the app role:
not an owner, not ``BYPASSRLS``, DML grants only.

m92 always installs the (inert) policies; ``ENABLE``/``FORCE ROW LEVEL
SECURITY`` happens only when ``DB_RLS_ENABLED=true`` at migration time, or
later with ``python -m app.migrations.org_authz_v1.rls_activation enable``.
Order: set ``DB_RLS_ENABLED=true`` for the app, restart it (sessions start
issuing transaction settings; nothing is enforced yet), then ``enable``. The
CLI refuses ``enable`` while its own environment has the flag off.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import anyio
import sqlalchemy as sa
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.migrations.org_authz_v1.rls_manifest import classified_tables
from app.migrations.org_authz_v1.rls_policies import (
    create_policy_sql,
    disable_sql,
    enable_sql,
    table_policy,
)

_OWNED = sa.text(
    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') "
    "AND pg_has_role(:role, c.relowner, 'MEMBER') ORDER BY c.relname"
)


class RlsRoleError(RuntimeError):
    """The connected roles cannot safely run RLS-forced tables."""


def app_role(connection: Connection) -> str | None:
    """The separate app role to grant, when role separation is configured."""
    if not settings.migration_database_url:
        return None
    role = make_url(settings.database_url).username
    current = connection.scalar(sa.text("SELECT current_user"))
    return role if role and role != current else None


def require_app_role_not_owner(connection: Connection, role: str) -> None:
    """An owner can disable RLS on its tables: ownership must move to the migration role."""
    exists = connection.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
    if not exists:
        raise RlsRoleError(f"DATABASE_URL role {role!r} does not exist; create it first")
    owned = list(connection.scalars(_OWNED, {"role": role}))
    if not owned:
        return
    quote = connection.dialect.identifier_preparer.quote
    target = str(connection.scalar(sa.text("SELECT current_user")))
    sample = "; ".join(f"ALTER TABLE {quote(name)} OWNER TO {quote(target)}" for name in owned[:3])
    raise RlsRoleError(
        f"DATABASE_URL role {role!r} owns {len(owned)} tables ({', '.join(owned[:5])}). "
        f"As a superuser run: REASSIGN OWNED BY {quote(role)} TO {quote(target)}; "
        f"(or per table: {sample}; ...) and rerun. See docs/operator-setup.md section 8."
    )


def require_rls_bypass(connection: Connection) -> None:
    """FORCE RLS applies to the owner too: the migration role must bypass it."""
    row = connection.execute(
        sa.text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    if not (row.rolsuper or row.rolbypassrls):
        raise RlsRoleError(
            "Enabling RLS needs a migration role with BYPASSRLS (MIGRATION_DATABASE_URL); "
            "otherwise later migrations would only see rows of no company"
        )


def install_policies(connection: Connection) -> tuple[str, ...]:
    """(Re)create ``tenant_boundary`` on every protected table; returns the tables."""
    quote = connection.dialect.identifier_preparer.quote
    tables = classified_tables(connection)
    for name in tables:
        for statement in create_policy_sql(quote, table_policy(quote, name)):
            connection.execute(sa.text(statement))
    return tables


def set_rls(connection: Connection, *, enabled: bool) -> tuple[str, ...]:
    """Refresh policies, then ENABLE+FORCE (or NO FORCE+DISABLE) on every protected table."""
    quote = connection.dialect.identifier_preparer.quote
    if enabled:
        require_rls_bypass(connection)
        role = app_role(connection)
        if role is not None:
            require_app_role_not_owner(connection, role)
        tables = install_policies(connection)
    else:
        tables = classified_tables(connection)
    for statement in (enable_sql if enabled else disable_sql)(quote, tables):
        connection.execute(sa.text(statement))
    return tables


async def _run(action: str, lock_timeout: str) -> int:
    url = settings.migration_database_url or settings.database_url
    if make_url(url).get_backend_name() != "postgresql":
        print("RLS is only supported on PostgreSQL")  # noqa: T201 - operator CLI
        return 2
    if action == "enable" and not settings.db_rls_enabled:
        print(  # noqa: T201 - operator CLI
            "Refusing to enable: set DB_RLS_ENABLED=true for the app and restart it first"
        )
        return 2
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text("SELECT set_config('lock_timeout', :t, true)"), {"t": lock_timeout}
            )
            tables = await connection.run_sync(
                lambda sync: set_rls(sync, enabled=action == "enable")
            )
    finally:
        await engine.dispose()
    print(f"RLS {action}d on {len(tables)} tables")  # noqa: T201 - operator CLI
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enable or disable m92 company RLS.")
    parser.add_argument("action", choices=("enable", "disable"))
    parser.add_argument("--lock-timeout", default="5s", help="per-table ALTER wait (default 5s)")
    args = parser.parse_args(argv)
    try:
        return anyio.run(_run, args.action, args.lock_timeout)
    except RlsRoleError as exc:
        print(exc)  # noqa: T201 - operator CLI
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
