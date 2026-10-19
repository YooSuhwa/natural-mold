"""m79 draft: same-organization groups and group membership."""

from schema_drafts.org_authz.migration_api import create_tables, drop_tables

revision = "m79_groups"
down_revision = "m78_tenants_organizations"
branch_labels = None
depends_on = None
TABLES = ("groups", "group_members")


def upgrade() -> None:
    create_tables(*TABLES)


def downgrade() -> None:
    drop_tables(*TABLES)
