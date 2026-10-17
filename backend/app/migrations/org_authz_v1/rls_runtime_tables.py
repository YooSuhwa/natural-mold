"""m92 (I-06): pre-create runtime-managed tables as the migration role.

With role separation the app role has no ``CREATE`` on the schema, but two
libraries create their tables at start-up: the LangGraph checkpointer
(``DATABASE_URL_SYNC``, ``AsyncPostgresSaver.setup``) and the APScheduler job
store. m92 creates them here, owned by the migration role, so the app role only
needs DML (``checkpointer.init_checkpointer`` skips ``setup()`` once every
LangGraph migration is recorded; APScheduler only creates when missing).

Tables created here are new and empty, so LangGraph's ``CREATE INDEX
CONCURRENTLY`` (not allowed in a migration transaction) runs without
``CONCURRENTLY``. Runtime tables stay outside RLS (``RUNTIME_EXEMPT``).
"""

from __future__ import annotations

import sqlalchemy as sa
from langgraph.checkpoint.postgres.base import BasePostgresSaver
from sqlalchemy.engine import Connection

APSCHEDULER_JOBS = sa.Table(
    "apscheduler_jobs",
    sa.MetaData(),
    # Same definition as apscheduler.jobstores.sqlalchemy.SQLAlchemyJobStore.
    sa.Column("id", sa.Unicode(191), primary_key=True),
    sa.Column("next_run_time", sa.Float(25), index=True),
    sa.Column("job_state", sa.LargeBinary, nullable=False),
)


def checkpoint_migrations_pending(version: int | None) -> range:
    """LangGraph migration indexes still to apply after recorded ``version``."""
    start = -1 if version is None else version
    return range(start + 1, len(BasePostgresSaver.MIGRATIONS))


def precreate_runtime_tables(connection: Connection) -> None:
    APSCHEDULER_JOBS.create(connection, checkfirst=True)
    migrations = BasePostgresSaver.MIGRATIONS
    connection.exec_driver_sql(migrations[0])
    version = connection.scalar(sa.text("SELECT max(v) FROM checkpoint_migrations"))
    for index in checkpoint_migrations_pending(version):
        connection.exec_driver_sql(migrations[index].replace("CONCURRENTLY ", ""))
        connection.execute(
            sa.text("INSERT INTO checkpoint_migrations (v) VALUES (:v)"), {"v": index}
        )
