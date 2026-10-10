"""m88 draft: requests and permission-filtered notification source rows."""

from schema_drafts.org_authz.migration_api import create_tables, drop_tables

revision = "m88_requests_notifications"
down_revision = "m87_credential_modes"
branch_labels = None
depends_on = None
TABLES = ("access_requests", "notifications")


def upgrade() -> None:
    create_tables(*TABLES)


def downgrade() -> None:
    drop_tables(*TABLES)
