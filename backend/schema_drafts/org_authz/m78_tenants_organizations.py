"""m78 draft: tenant and organization relationship sources."""

from schema_drafts.org_authz.migration_api import create_tables, drop_tables

revision = "m78_tenants_organizations"
down_revision = "m77_side_chat_link"
branch_labels = None
depends_on = None
TABLES = (
    "tenants",
    "tenant_members",
    "tenant_role_grants",
    "organizations",
    "organization_members",
    "organization_role_grants",
)


def upgrade() -> None:
    create_tables(*TABLES)


def downgrade() -> None:
    drop_tables(*TABLES)
