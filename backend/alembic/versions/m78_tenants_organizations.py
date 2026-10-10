"""B-01: immutable reviewed m78_tenants_organizations DDL."""

import sqlalchemy as sa

from alembic import op

revision = "m78_tenants_organizations"
down_revision = "m77_side_chat_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("limits", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("settings", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("secrets_namespace", sa.String(length=250), nullable=False),
        sa.Column("data_keys", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("deletion_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','deletion_scheduled')", name=op.f("ck_tenants_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("secrets_namespace", name=op.f("uq_tenants_secrets_namespace")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_table(
        "tenant_members",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','removed')", name=op.f("ck_tenant_members_status")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_members_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tenant_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", name=op.f("pk_tenant_members")),
    )
    op.create_table(
        "tenant_role_grants",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('admin','auditor')", name=op.f("ck_tenant_role_grants_role")),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_tenant_role_grants_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_role_grants_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tenant_role_grants_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_role_grants")),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "role", name=op.f("uq_tenant_role_grants_tenant_id")
        ),
    )
    op.create_table(
        "organizations",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("settings", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("monthly_spend_limit", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("authz_epoch", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','archived')", name=op.f("ck_organizations_status")
        ),
        sa.CheckConstraint("authz_epoch >= 0", name=op.f("ck_organizations_authz_epoch")),
        sa.CheckConstraint(
            "monthly_spend_limit IS NULL OR monthly_spend_limit >= 0",
            name=op.f("ck_organizations_monthly_spend_limit"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_organizations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_organizations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("id", "tenant_id", name=op.f("uq_organizations_id")),
        sa.UniqueConstraint("tenant_id", "slug", name=op.f("uq_organizations_tenant_id")),
    )
    op.create_table(
        "organization_members",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("monthly_spend_limit", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("invited_by", sa.Uuid(), nullable=True),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','suspended','removed')",
            name=op.f("ck_organization_members_status"),
        ),
        sa.CheckConstraint(
            "monthly_spend_limit IS NULL OR monthly_spend_limit >= 0",
            name=op.f("ck_organization_members_monthly_spend_limit"),
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name=op.f("fk_organization_members_invited_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=op.f("fk_organization_members_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_organization_members_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_organization_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("org_id", "user_id", name=op.f("pk_organization_members")),
    )
    op.create_table(
        "organization_role_grants",
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("principal_type", sa.String(length=20), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "principal_type IN ('user','group')",
            name=op.f("ck_organization_role_grants_principal_type"),
        ),
        sa.CheckConstraint(
            "role IN ('admin','builder','auditor')", name=op.f("ck_organization_role_grants_role")
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_organization_role_grants_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=op.f("fk_organization_role_grants_org_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_organization_role_grants_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_role_grants")),
        sa.UniqueConstraint(
            "org_id",
            "role",
            "principal_type",
            "principal_id",
            name=op.f("uq_organization_role_grants_org_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("organization_role_grants")
    op.drop_table("organization_members")
    op.drop_table("organizations")
    op.drop_table("tenant_role_grants")
    op.drop_table("tenant_members")
    op.drop_table("tenants")
