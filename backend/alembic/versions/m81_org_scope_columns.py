"""Apply the frozen organization scope contract."""

from app.migrations.org_authz_v1 import m81_scope_ddl

revision = "m81_org_scope_columns"
down_revision = "m80_org_capabilities_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    m81_scope_ddl.upgrade()


def downgrade() -> None:
    m81_scope_ddl.downgrade()
