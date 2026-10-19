"""Apply the frozen organization scope contract."""

from app.migrations.org_authz_v1 import m83_scope_ddl

revision = "m83_org_scope_not_null"
down_revision = "m82_default_org_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    m83_scope_ddl.upgrade()


def downgrade() -> None:
    m83_scope_ddl.downgrade()
