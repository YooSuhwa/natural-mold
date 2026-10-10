"""B-01: immutable reviewed m80_org_capabilities_invitations DDL."""

import sqlalchemy as sa

from alembic import op

revision = "m80_org_capabilities_invitations"
down_revision = "m79_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_capability_grants",
        sa.Column("capability", sa.String(length=40), nullable=False),
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
            "capability IN "
            "('cap_create_agent','cap_create_skill','cap_create_tool','cap_register_mcp',"
            "'cap_connect_database','cap_share_org_wide','cap_publish_marketplace')",
            name=op.f("ck_org_capability_grants_capability"),
        ),
        sa.CheckConstraint(
            "principal_type != 'organization' OR principal_id = org_id",
            name=op.f("ck_org_capability_grants_organization_principal"),
        ),
        sa.CheckConstraint(
            "principal_type IN ('user','group','organization')",
            name=op.f("ck_org_capability_grants_principal_type"),
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_org_capability_grants_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=op.f("fk_org_capability_grants_org_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_org_capability_grants_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_capability_grants")),
        sa.UniqueConstraint(
            "org_id",
            "capability",
            "principal_type",
            "principal_id",
            name=op.f("uq_org_capability_grants_org_id"),
        ),
    )
    op.create_table(
        "org_invitations",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="member", nullable=False),
        sa.Column("group_ids", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "role IN ('member','builder','admin','auditor')", name=op.f("ck_org_invitations_role")
        ),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_org_invitations_expiration")),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name=op.f("fk_org_invitations_accepted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name=op.f("fk_org_invitations_invited_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=op.f("fk_org_invitations_org_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_org_invitations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_org_invitations_token_hash")),
    )
    op.create_table(
        "tenant_domains",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("txt_token", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending','verified','disabled')", name=op.f("ck_tenant_domains_status")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_domains_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_domains")),
        sa.UniqueConstraint("domain", name=op.f("uq_tenant_domains_domain")),
    )
    op.create_table(
        "sso_connections",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("protocol", sa.String(length=20), server_default="oidc", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("issuer", sa.String(length=1000), nullable=False),
        sa.Column("client_id", sa.String(length=500), nullable=True),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("ldap_server_url", sa.String(length=1000), nullable=True),
        sa.Column("ldap_start_tls", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ldap_bind_dn", sa.String(length=1000), nullable=True),
        sa.Column("ldap_bind_password_encrypted", sa.Text(), nullable=True),
        sa.Column("ldap_user_base_dn", sa.String(length=1000), nullable=True),
        sa.Column("ldap_user_filter", sa.Text(), nullable=True),
        sa.Column("ldap_group_base_dn", sa.String(length=1000), nullable=True),
        sa.Column("ldap_group_filter", sa.Text(), nullable=True),
        sa.Column("ldap_attribute_mapping", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("ldap_ca_certificate", sa.Text(), nullable=True),
        sa.Column("key_id", sa.String(length=16), nullable=True),
        sa.Column(
            "scopes", sa.JSON(), server_default='["openid","email","profile"]', nullable=False
        ),
        sa.Column("claim_mapping", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("group_role_mapping", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("jit_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("default_role", sa.String(length=20), server_default="member", nullable=False),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
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
            "default_role IN ('member','builder','admin','auditor')",
            name=op.f("ck_sso_connections_default_role"),
        ),
        sa.CheckConstraint(
            "ldap_server_url IS NULL OR ldap_server_url LIKE 'ldaps://%' OR "
            "(ldap_server_url LIKE 'ldap://%' AND ldap_start_tls = true)",
            name=op.f("ck_sso_connections_ldap_transport"),
        ),
        sa.CheckConstraint(
            "protocol <> 'ldap' OR status IN ('draft','disabled') OR (ldap_server_url IS "
            "NOT NULL AND ldap_bind_dn IS NOT NULL AND ldap_bind_password_encrypted IS "
            "NOT NULL AND key_id IS NOT NULL AND ldap_user_base_dn IS NOT NULL AND "
            "ldap_user_filter IS NOT NULL)",
            name=op.f("ck_sso_connections_ldap_configuration"),
        ),
        sa.CheckConstraint("protocol IN ('oidc','ldap')", name=op.f("ck_sso_connections_protocol")),
        sa.CheckConstraint(
            "status IN ('draft','tested','active','disabled')",
            name=op.f("ck_sso_connections_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_sso_connections_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sso_connections")),
    )
    op.create_table(
        "user_identities",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=True),
        sa.Column("issuer", sa.String(length=1000), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["sso_connections.id"],
            name=op.f("fk_user_identities_connection_id_sso_connections"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identities")),
        sa.UniqueConstraint("issuer", "subject", name=op.f("uq_user_identities_issuer")),
    )

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
    op.drop_table("user_identities")
    op.drop_table("sso_connections")
    op.drop_table("tenant_domains")
    op.drop_table("org_invitations")
    op.drop_table("org_capability_grants")
