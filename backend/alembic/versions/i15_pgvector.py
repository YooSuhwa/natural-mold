"""Enable the pgvector extension where the database offers it (feature plan v2 순번 5).

The Compose database image (docker/postgres) ships pgvector. A database without it (an
existing install on another image, or a managed service that has not enabled it) is left
as it is: nothing uses vectors yet, and the operator's system status reports the missing
extension (``app.install.checks``). Creating it needs a role allowed to create extensions;
without that right the step is skipped the same way, in a savepoint so the migration
transaction goes on. Downgrade leaves the extension: other objects may depend on it.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

revision = "i15_pgvector"
down_revision = "i14_jobs"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    available = bind.scalar(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"))
    if not available:
        logger.warning("pgvector is not installed in this database; vector search stays off")
        return
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    except sa.exc.DBAPIError:
        logger.warning("could not create the pgvector extension (needs the right to create it)")


def downgrade() -> None:
    pass
