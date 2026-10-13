"""Add company invitations and organization archive execution markers."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b11_company_administration"
down_revision = "b08_agent_lifecycle"
branch_labels = None
depends_on = None


def _hex64_sql(column_name: str) -> str:
    remainder = column_name
    for digit in "0123456789abcdef":
        remainder = f"replace({remainder}, '{digit}', '')"
    return f"length({column_name}) = 64 AND {remainder} = ''"


def _create_tenant_invitations() -> None:
    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_by", sa.Uuid(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "email = lower(trim(email))",
            name=op.f("ck_tenant_invitations_email_normalized"),
        ),
        sa.CheckConstraint(
            "role IS NULL OR role IN ('admin','auditor')",
            name=op.f("ck_tenant_invitations_role"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','revoked')",
            name=op.f("ck_tenant_invitations_status"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_tenant_invitations_expiration"),
        ),
        sa.CheckConstraint(
            "accepted_by IS NULL OR accepted_at IS NOT NULL",
            name=op.f("ck_tenant_invitations_accepted_actor_timestamp"),
        ),
        sa.CheckConstraint(
            "revoked_by IS NULL OR revoked_at IS NOT NULL",
            name=op.f("ck_tenant_invitations_revoked_actor_timestamp"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND accepted_at IS NULL AND revoked_at IS NULL) OR "
            "(status = 'accepted' AND accepted_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND accepted_at IS NULL AND revoked_at IS NOT NULL)",
            name=op.f("ck_tenant_invitations_terminal_state"),
        ),
        sa.CheckConstraint(
            _hex64_sql("token_hash"),
            name=op.f("ck_tenant_invitations_token_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_invitations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_tenant_invitations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name=op.f("fk_tenant_invitations_accepted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name=op.f("fk_tenant_invitations_revoked_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_tenant_invitations_token_hash")),
    )
    op.create_index(
        "ix_tenant_invitations_pending",
        "tenant_invitations",
        ["tenant_id", "email", "role"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )


def _add_organization_archive_state() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            "UPDATE organizations SET archived_at = updated_at "
            "WHERE status = 'archived' AND archived_at IS NULL"
        )
    )
    with op.batch_alter_table("organizations") as batch:
        batch.create_check_constraint(
            "ck_organizations_archive_state",
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status <> 'archived' AND archived_at IS NULL)",
        )


def _add_run_materialization_pair() -> None:
    with op.batch_alter_table("conversation_runs") as batch:
        batch.add_column(
            sa.Column("runtime_materialized_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("runtime_materialization_sha256", sa.String(length=64), nullable=True)
        )
        batch.create_check_constraint(
            "ck_conversation_runs_runtime_materialization_pair",
            "(runtime_materialized_at IS NULL AND runtime_materialization_sha256 IS NULL) OR "
            "(runtime_materialized_at IS NOT NULL "
            "AND runtime_materialization_sha256 IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_conversation_runs_runtime_materialization_sha256",
            "runtime_materialization_sha256 IS NULL OR "
            f"({_hex64_sql('runtime_materialization_sha256')})",
        )


def upgrade() -> None:
    _create_tenant_invitations()
    _add_organization_archive_state()
    _add_run_materialization_pair()


def downgrade() -> None:
    with op.batch_alter_table("conversation_runs") as batch:
        batch.drop_constraint("ck_conversation_runs_runtime_materialization_sha256", type_="check")
        batch.drop_constraint("ck_conversation_runs_runtime_materialization_pair", type_="check")
        batch.drop_column("runtime_materialization_sha256")
        batch.drop_column("runtime_materialized_at")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_constraint("ck_organizations_archive_state", type_="check")
        batch.drop_column("archived_at")
    op.drop_index("ix_tenant_invitations_pending", table_name="tenant_invitations")
    op.drop_table("tenant_invitations")
