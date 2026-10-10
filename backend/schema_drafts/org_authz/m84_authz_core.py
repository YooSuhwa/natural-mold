"""m84 draft: authorization source grants, projection queue and model pinning."""

from schema_drafts.org_authz.migration_api import create_tables, drop_tables

revision = "m84_authz_core"
down_revision = "m83_org_scope_not_null"
branch_labels = None
depends_on = None
TABLES = (
    "resource_grants",
    "authz_outbox",
    "authz_revocations",
    "authz_model_versions",
    "authz_shadow_diffs",
)


def upgrade() -> None:
    create_tables(*TABLES)


def downgrade() -> None:
    drop_tables(*TABLES)
