"""m85 draft: retain legacy ACL while projecting exact original grant relations."""

from alembic import op
from schema_drafts.org_authz.marketplace_grant_backfill import copy_grants, reverse_grants

revision = "m85_marketplace_acl_to_grants"
down_revision = "m84_authz_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    copy_grants(op.get_bind())


def downgrade() -> None:
    reverse_grants(op.get_bind())
