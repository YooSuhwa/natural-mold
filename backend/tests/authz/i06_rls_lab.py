"""Disposable PostgreSQL database migrated to head with m92 RLS on (I-06 lane only).

The lane role (superuser, owner) plays the migration role; a fresh LOGIN role
without BYPASSRLS plays the app role, so policies really apply to app SQL.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import URL, Engine, make_url

from alembic import command
from app.config import settings

BACKEND = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RlsLab:
    admin: Engine  # migration role: owner, bypasses RLS
    admin_url: URL
    app_url: URL  # app role: DML grants only, NOBYPASSRLS
    role: str
    tenant_a: UUID
    tenant_b: UUID


def async_url(url: URL) -> str:
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def _seed_companies(admin: Engine, *tenants: UUID) -> None:
    with admin.begin() as connection:
        for tenant in tenants:
            connection.execute(
                sa.text(
                    "INSERT INTO tenants (id,name,slug,status,limits,settings,secrets_namespace,"
                    "data_keys,created_at,updated_at) VALUES (:id,'Company',:slug,'active','{}',"
                    "'{}',:ns,'{}',now(),now())"
                ),
                {"id": tenant, "slug": f"c-{tenant.hex[:12]}", "ns": f"tenants/{tenant}"},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO tenant_domains (id,tenant_id,domain,txt_token,status,created_at,"
                    "updated_at) VALUES (:id,:tenant,:domain,'token','pending',now(),now())"
                ),
                {"id": uuid4(), "tenant": tenant, "domain": f"{tenant.hex[:12]}.example.test"},
            )


@contextmanager
def rls_lab() -> Iterator[RlsLab]:
    source = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    database = "moldy_pg_lane_i06_" + uuid4().hex
    role, password = "moldy_i06_app_" + uuid4().hex[:12], uuid4().hex
    root = sa.create_engine(source, isolation_level="AUTOCOMMIT")
    target = source.set(database=database)
    app_url = target.set(username=role, password=password)
    admin = sa.create_engine(target)
    created = False
    try:
        with root.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
            created = True
            connection.execute(
                sa.text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}' NOBYPASSRLS")
            )
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        config.attributes["database_url"] = async_url(target)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(settings, "db_rls_enabled", True)
            patch.setattr(settings, "database_url", async_url(app_url))
            patch.setattr(settings, "migration_database_url", async_url(target))
            # env.py drives asyncio.run; keep it off any running event loop.
            with ThreadPoolExecutor(1) as pool:
                pool.submit(command.upgrade, config, "head").result()
        lab = RlsLab(admin, target, app_url, role, uuid4(), uuid4())
        _seed_companies(admin, lab.tenant_a, lab.tenant_b)
        yield lab
    finally:
        admin.dispose()
        if created:
            with root.connect() as connection:
                connection.execute(sa.text(f'DROP DATABASE "{database}" WITH (FORCE)'))
                connection.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        root.dispose()
