"""Deferred PR2 draft: remove shadow differences after the signed-off report."""

from alembic import op
from schema_drafts.org_authz.rollout_gate import require_rollout_receipt

revision = "m91_drop_shadow_diffs"
down_revision = "m90_drop_legacy_authz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    require_rollout_receipt(stabilization=True)
    op.drop_table("authz_shadow_diffs")


def downgrade() -> None:
    raise ValueError("m91 removes observation data; restore the verified backup")
