"""C-01 (plan m84): authorization grants, projection outbox, revocations, model pins."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c01_authz_core"
down_revision = "b11_company_administration"
branch_labels = None
depends_on = None

_RESOURCE_TYPES = (
    "'agent','skill','tool','mcp_server','mcp_tool','credential','marketplace_item',"
    "'knowledge_base','document','db_connection','db_query_tool','workflow','llm_model'"
)
_RELATIONS = "'consumer','viewer','editor','manager','installer','explorer','blocked','allowed_org'"


def _now() -> sa.TextClause:
    return sa.text("CURRENT_TIMESTAMP")


def _scope(table: str) -> list[sa.SchemaItem]:
    return [
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=f"fk_{table}_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{table}_org_id_organizations",
            ondelete="RESTRICT",
        ),
    ]


def _actor(table: str, column: str) -> list[sa.SchemaItem]:
    return [
        sa.Column(column, sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            [column], ["users.id"], name=f"fk_{table}_{column}_users", ondelete="SET NULL"
        ),
    ]


def _create_resource_grants() -> None:
    op.create_table(
        "resource_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scope("resource_grants"),
        sa.Column("state", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=20), nullable=False),
        sa.Column("principal_type", sa.String(length=20), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("grant_kind", sa.String(length=20), server_default="share", nullable=False),
        sa.Column("condition_name", sa.String(length=80), nullable=True),
        sa.Column("condition_context", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        *_actor("resource_grants", "granted_by"),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        *_actor("resource_grants", "revoked_by"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_link", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "state IN ('active','revoking','revoked')", name="ck_resource_grants_state"
        ),
        sa.CheckConstraint(
            f"resource_type IN ({_RESOURCE_TYPES})", name="ck_resource_grants_resource_type"
        ),
        sa.CheckConstraint(f"relation IN ({_RELATIONS})", name="ck_resource_grants_relation"),
        sa.CheckConstraint(
            "principal_type IN ('user','group','organization','agent')",
            name="ck_resource_grants_principal_type",
        ),
        sa.CheckConstraint(
            "relation <> 'allowed_org' OR principal_type = 'organization'",
            name="ck_resource_grants_allowed_org_principal",
        ),
        sa.CheckConstraint(
            "grant_kind IN ('share','delegation','system_fanout','migration')",
            name="ck_resource_grants_grant_kind",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resource_grants"),
    )
    op.create_index(
        "uq_resource_grants_active",
        "resource_grants",
        ["resource_type", "resource_id", "relation", "principal_type", "principal_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        sqlite_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "ix_resource_grants_resource", "resource_grants", ["org_id", "resource_type", "resource_id"]
    )
    op.create_index(
        "ix_resource_grants_principal", "resource_grants", ["principal_type", "principal_id"]
    )
    op.create_index(
        "ix_resource_grants_delegator",
        "resource_grants",
        ["granted_by", "resource_type", "resource_id"],
    )


def _create_outbox() -> None:
    op.create_table(
        "authz_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("op", sa.String(length=10), nullable=False),
        sa.Column("tuple", sa.JSON(), nullable=False),
        sa.Column("source_table", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("op IN ('write','delete')", name="ck_authz_outbox_op"),
        sa.CheckConstraint("attempts >= 0", name="ck_authz_outbox_attempts"),
        sa.PrimaryKeyConstraint("id", name="pk_authz_outbox"),
    )
    op.create_index(
        "ix_authz_outbox_unprocessed",
        "authz_outbox",
        ["next_attempt_at", "created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
        sqlite_where=sa.text("processed_at IS NULL"),
    )


def _create_revocations() -> None:
    op.create_table(
        "authz_revocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        *_scope("authz_revocations"),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("object", sa.String(length=100), nullable=False),
        sa.Column("fga_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scope IN ('grant','membership','group_member','role','capability',"
            "'ownership','resource')",
            name="ck_authz_revocations_scope",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_authz_revocations"),
    )
    op.create_index(
        "ix_authz_revocations_pending",
        "authz_revocations",
        ["org_id", "subject", "object"],
        postgresql_where=sa.text("finalized_at IS NULL"),
        sqlite_where=sa.text("finalized_at IS NULL"),
    )


def _create_model_versions() -> None:
    op.create_table(
        "authz_model_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.String(length=26), nullable=False),
        sa.Column("model_id", sa.String(length=26), nullable=False),
        sa.Column("model_sha256", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_authz_model_versions"),
        sa.UniqueConstraint("model_id", name="uq_authz_model_versions_model_id"),
    )
    op.create_index(
        "uq_authz_model_versions_active",
        "authz_model_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active IS true"),
        sqlite_where=sa.text("is_active IS 1"),
    )


def _create_shadow_diffs() -> None:
    op.create_table(
        "authz_shadow_diffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("route", sa.String(length=255), nullable=False),
        sa.Column("principal", sa.String(length=100), nullable=False),
        sa.Column("object", sa.String(length=100), nullable=False),
        sa.Column("permission", sa.String(length=80), nullable=False),
        sa.Column("legacy", sa.Boolean(), nullable=False),
        sa.Column("fga", sa.Boolean(), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("count", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("count > 0", name="ck_authz_shadow_diffs_count"),
        sa.PrimaryKeyConstraint("id", name="pk_authz_shadow_diffs"),
        sa.UniqueConstraint(
            "route",
            "principal",
            "object",
            "permission",
            "legacy",
            "fga",
            name="uq_authz_shadow_diffs_key",
        ),
    )


def upgrade() -> None:
    _create_resource_grants()
    _create_outbox()
    _create_revocations()
    _create_model_versions()
    _create_shadow_diffs()


def downgrade() -> None:
    op.drop_table("authz_shadow_diffs")
    op.drop_index("uq_authz_model_versions_active", table_name="authz_model_versions")
    op.drop_table("authz_model_versions")
    op.drop_index("ix_authz_revocations_pending", table_name="authz_revocations")
    op.drop_table("authz_revocations")
    op.drop_index("ix_authz_outbox_unprocessed", table_name="authz_outbox")
    op.drop_table("authz_outbox")
    for index in (
        "ix_resource_grants_delegator",
        "ix_resource_grants_principal",
        "ix_resource_grants_resource",
        "uq_resource_grants_active",
    ):
        op.drop_index(index, table_name="resource_grants")
    op.drop_table("resource_grants")
