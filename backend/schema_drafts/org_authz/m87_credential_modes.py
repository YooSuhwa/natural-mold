"""m87 draft: credential modes and explicit destination host lists."""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.credential_mode_backfill import backfill_modes
from schema_drafts.org_authz.migration_api import create_tables, drop_tables

revision = "m87_credential_modes"
down_revision = "m86_agent_links_delegation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("tools", "mcp_servers"):
        with op.batch_alter_table(name) as batch:
            batch.add_column(
                sa.Column("credential_mode", sa.String(20), nullable=False, server_default="shared")
            )
            batch.add_column(sa.Column("credential_definition_key", sa.String(80), nullable=True))
            batch.create_check_constraint(
                f"ck_{name}_credential_mode", "credential_mode IN ('none','shared','per_user')"
            )
    with op.batch_alter_table("credentials") as batch:
        batch.add_column(sa.Column("allowed_hosts", sa.JSON(), nullable=False, server_default="[]"))
    create_tables("resource_credential_bindings")
    backfill_modes(op.get_bind())


def downgrade() -> None:
    drop_tables("resource_credential_bindings")
    with op.batch_alter_table("credentials") as batch:
        batch.drop_column("allowed_hosts")
    for name in ("mcp_servers", "tools"):
        with op.batch_alter_table(name) as batch:
            batch.drop_constraint(f"ck_{name}_credential_mode", type_="check")
            batch.drop_column("credential_mode")
            batch.drop_column("credential_definition_key")
