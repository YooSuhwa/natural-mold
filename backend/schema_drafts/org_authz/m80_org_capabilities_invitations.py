"""m80 draft: capabilities, invitations, tenant identity and active organization.

The original 6.3 list omitted all three SSO tables despite 6.1 requiring them.
These tenant identity definitions fit before scope migration without another head.
"""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.migration_api import create_tables, drop_tables

revision = "m80_org_capabilities_invitations"
down_revision = "m79_groups"
branch_labels = None
depends_on = None
TABLES = (
    "org_capability_grants",
    "org_invitations",
    "tenant_domains",
    "sso_connections",
    "user_identities",
)


def upgrade() -> None:
    create_tables(*TABLES)
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_active_org_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_users_last_active_org",
            "organizations",
            ["last_active_org_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_last_active_org", type_="foreignkey")
        batch.drop_column("last_active_org_id")
    drop_tables(*TABLES)
