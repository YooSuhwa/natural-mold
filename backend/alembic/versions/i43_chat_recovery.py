"""Fence chat workers and record tool invocations for crash recovery."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i43_chat_recovery"
down_revision = "i39_skill_approval_default"
branch_labels = None
depends_on = None

_RUN = "conversation_runs"
_TABLE = "conversation_run_tool_receipts"


def upgrade() -> None:
    op.add_column(
        _RUN,
        sa.Column(
            "owner_generation",
            sa.Integer(),
            sa.CheckConstraint("owner_generation >= 0", name="ck_run_owner_generation"),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(_RUN, sa.Column("owner_token", sa.Uuid(), nullable=True))
    op.add_column(
        _RUN,
        sa.Column(
            "recovery_attempts",
            sa.Integer(),
            sa.CheckConstraint("recovery_attempts >= 0", name="ck_run_recovery_attempts"),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(_RUN, sa.Column("recovery_runtime_digest", sa.String(64), nullable=True))
    op.create_index(
        "uq_conversation_runs_id_scope", _RUN, ["id", "tenant_id", "org_id"], unique=True
    )
    op.create_table(
        _TABLE,
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.String(255), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("arguments_sha256", sa.String(64), nullable=False),
        sa.Column("effect_kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("owner_generation", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("run_id", "tool_call_id", name="pk_conversation_run_tool_receipts"),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id", "org_id"],
            ["conversation_runs.id", "conversation_runs.tenant_id", "conversation_runs.org_id"],
            name="fk_run_tool_receipt_scoped_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "effect_kind IN ('read','idempotent_write','external_write')",
            name="ck_run_tool_receipt_effect",
        ),
        sa.CheckConstraint("status IN ('started','completed')", name="ck_run_tool_receipt_status"),
        sa.CheckConstraint("owner_generation > 0", name="ck_run_tool_receipt_generation"),
        sa.CheckConstraint("length(arguments_sha256) = 64", name="ck_run_tool_receipt_digest"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        quote = bind.dialect.identifier_preparer.quote
        for statement in create_policy_sql(quote, table_policy(quote, _TABLE)):
            op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, [_TABLE]):
                op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table(_TABLE)
    op.drop_index("uq_conversation_runs_id_scope", table_name=_RUN)
    op.drop_column(_RUN, "recovery_runtime_digest")
    op.drop_column(_RUN, "recovery_attempts")
    op.drop_column(_RUN, "owner_token")
    op.drop_column(_RUN, "owner_generation")
