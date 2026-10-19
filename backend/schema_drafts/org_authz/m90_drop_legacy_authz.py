"""Deferred PR2 draft: remove legacy columns only after observed stabilization."""

from alembic import op
from schema_drafts.org_authz.rollout_gate import require_rollout_receipt

revision = "m90_drop_legacy_authz"
down_revision = "m92_tenant_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    require_rollout_receipt(stabilization=True)
    op.drop_table("marketplace_item_acl")
    with op.batch_alter_table("credentials") as batch:
        batch.drop_column("is_shared")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("is_favorite")


def downgrade() -> None:
    raise ValueError("m90 removes data; restore the verified pre-cleanup backup")
