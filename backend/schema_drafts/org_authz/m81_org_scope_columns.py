"""m81 draft: nullable scope, independent conversation ownership and tenant catalogs."""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.catalog_constraints import downgrade_constraints, upgrade_constraints
from schema_drafts.org_authz.scope_columns import (
    M81_SCOPED,
    NAMING,
    TENANT_ONLY,
    add_org_pair,
    drop_org_pair,
)

revision = "m81_org_scope_columns"
down_revision = "m80_org_capabilities_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in M81_SCOPED:
        add_org_pair(name)
    for name in TENANT_ONLY:
        with op.batch_alter_table(name, naming_convention=NAMING) as batch:
            batch.add_column(sa.Column("tenant_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                f"fk_{name}_tenant", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT"
            )
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_conversations_owner", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index(
            "ix_conversations_owner_org_updated", ["user_id", "org_id", "updated_at"]
        )
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("archived_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_agents_archived_by", "users", ["archived_by"], ["id"], ondelete="SET NULL"
        )
        batch.add_column(sa.Column("monthly_spend_limit", sa.Numeric(20, 8), nullable=True))
        batch.create_check_constraint(
            "ck_agents_monthly_spend_limit",
            "monthly_spend_limit IS NULL OR monthly_spend_limit >= 0",
        )
    with op.batch_alter_table("credentials") as batch:
        batch.add_column(
            sa.Column("scope", sa.String(20), nullable=False, server_default="personal")
        )
        batch.create_check_constraint(
            "ck_credentials_scope", "scope IN ('personal','tenant','platform')"
        )
    with op.batch_alter_table("marketplace_items") as batch:
        batch.add_column(
            sa.Column("tenant_publication", sa.String(20), nullable=False, server_default="none")
        )
        batch.create_check_constraint(
            "ck_marketplace_items_tenant_publication",
            "tenant_publication IN ('none','pending','approved','rejected')",
        )
        for action in ("requested", "reviewed"):
            batch.add_column(sa.Column(f"tenant_publication_{action}_by", sa.Uuid(), nullable=True))
            batch.add_column(
                sa.Column(
                    f"tenant_publication_{action}_at", sa.DateTime(timezone=True), nullable=True
                )
            )
            batch.create_foreign_key(
                f"fk_marketplace_items_publication_{action}_by",
                "users",
                [f"tenant_publication_{action}_by"],
                ["id"],
                ondelete="SET NULL",
            )
        batch.add_column(sa.Column("tenant_publication_note", sa.Text(), nullable=True))
    upgrade_constraints()


def downgrade() -> None:
    downgrade_constraints()
    with op.batch_alter_table("marketplace_items") as batch:
        batch.drop_constraint("ck_marketplace_items_tenant_publication", type_="check")
        for action in ("requested", "reviewed"):
            batch.drop_constraint(
                f"fk_marketplace_items_publication_{action}_by", type_="foreignkey"
            )
            batch.drop_column(f"tenant_publication_{action}_by")
            batch.drop_column(f"tenant_publication_{action}_at")
        batch.drop_column("tenant_publication_note")
        batch.drop_column("tenant_publication")
    with op.batch_alter_table("credentials") as batch:
        batch.drop_constraint("ck_credentials_scope", type_="check")
        batch.drop_column("scope")
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint("fk_agents_archived_by", type_="foreignkey")
        batch.drop_constraint("ck_agents_monthly_spend_limit", type_="check")
        batch.drop_column("monthly_spend_limit")
        batch.drop_column("archived_by")
        batch.drop_column("archived_at")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_index("ix_conversations_owner_org_updated")
        batch.drop_constraint("fk_conversations_owner", type_="foreignkey")
        batch.drop_column("user_id")
    for name in reversed(TENANT_ONLY):
        with op.batch_alter_table(name) as batch:
            batch.drop_constraint(f"fk_{name}_tenant", type_="foreignkey")
            batch.drop_column("tenant_id")
    for name in reversed(M81_SCOPED):
        drop_org_pair(name)
