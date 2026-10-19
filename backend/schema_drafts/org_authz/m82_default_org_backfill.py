"""m82 draft: preserve existing creators in the default tenant and organization."""

from alembic import op
from schema_drafts.org_authz.default_org_backfill import backfill, reverse_backfill

revision = "m82_default_org_backfill"
down_revision = "m81_org_scope_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    backfill(op.get_bind())


def downgrade() -> None:
    reverse_backfill(op.get_bind())
