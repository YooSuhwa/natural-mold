"""An owned PostgreSQL database for testing the deployable organization chain."""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import anyio
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from anyio.to_thread import run_sync
from sqlalchemy.engine import Engine, make_url

from alembic import command
from tests.authz.head_template_database import create_head_database


def alembic_head() -> str:
    """The single current Alembic head the live app's ORM mappings are written against."""
    backend = Path(__file__).resolve().parents[2]
    head = ScriptDirectory.from_config(Config(str(backend / "alembic.ini"))).get_current_head()
    assert head is not None
    return head


@dataclass(frozen=True, slots=True)
class OrganizationLab:
    engine: Engine
    config: Config


@contextmanager
def organization_lab(*, at_head: bool = False) -> Iterator[OrganizationLab]:
    """An owned database at m80, or (``at_head``) an empty one cloned at the Alembic head."""
    source = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    database = "moldy_pg_lane_orgs_" + uuid4().hex
    admin = sa.create_engine(source, isolation_level="AUTOCOMMIT")
    engine = sa.create_engine(source.set(database=database))
    created = False
    try:
        if at_head:
            create_head_database(source, database)
            created = True
        else:
            with admin.connect() as connection:
                connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
                created = True
        backend = Path(__file__).resolve().parents[2]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "alembic"))
        config.attributes["database_url"] = source.set(
            database=database, drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False)
        if not at_head:
            command.upgrade(config, "m80_org_capabilities_invitations")
        yield OrganizationLab(engine, config)
    finally:
        engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(sa.text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


@asynccontextmanager
async def async_organization_lab(*, at_head: bool = False) -> AsyncIterator[OrganizationLab]:
    """Run Alembic off the active event loop and always remove the owned database."""
    stack = ExitStack()
    try:
        lab = await run_sync(stack.enter_context, organization_lab(at_head=at_head))
        yield lab
    finally:
        with anyio.CancelScope(shield=True):
            await run_sync(stack.close)
