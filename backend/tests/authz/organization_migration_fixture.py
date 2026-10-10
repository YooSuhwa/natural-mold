"""An owned PostgreSQL database for testing the deployable organization chain."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import Engine, make_url

from alembic import command


@dataclass(frozen=True, slots=True)
class OrganizationLab:
    engine: Engine
    config: Config


@contextmanager
def organization_lab() -> Iterator[OrganizationLab]:
    source = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    database = "moldy_pg_lane_orgs_" + uuid4().hex
    admin = sa.create_engine(source, isolation_level="AUTOCOMMIT")
    engine = sa.create_engine(source.set(database=database))
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
            created = True
        backend = Path(__file__).resolve().parents[2]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "alembic"))
        config.attributes["database_url"] = source.set(
            database=database, drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False)
        command.upgrade(config, "m80_org_capabilities_invitations")
        yield OrganizationLab(engine, config)
    finally:
        engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(sa.text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()
