"""Persist ordered same-run chat instructions and their delivery receipts."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_activation import app_role
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i40_chat_run_steers"
down_revision = "i39_skill_approval_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_run_steers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_branch_checkpoint_id", sa.String(64)),
        sa.Column("delivery_started_at", sa.DateTime()),
        sa.Column("applied_at", sa.DateTime()),
        sa.Column("queued_input_id", sa.Uuid(), sa.ForeignKey("conversation_run_inputs.id")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_conversation_run_steers_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name="fk_conversation_run_steers_org_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("conversation_id", "client_request_id", name="uq_run_steers_request"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_run_steers_sequence"),
        sa.UniqueConstraint("queued_input_id", name="uq_run_steers_queued_input"),
        sa.CheckConstraint("sequence >= 1", name="ck_run_steers_sequence"),
        sa.CheckConstraint("length(text) BETWEEN 1 AND 12000", name="ck_run_steers_text_length"),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'queued', 'needs_review')",
            name="ck_run_steers_status",
        ),
        sa.CheckConstraint(
            "(status = 'queued') = (queued_input_id IS NOT NULL)", name="ck_run_steers_queue"
        ),
        sa.CheckConstraint(
            "(status = 'applied') = (applied_at IS NOT NULL)", name="ck_run_steers_applied"
        ),
    )
    op.create_index(
        "ix_run_steers_delivery", "conversation_run_steers", ["run_id", "status", "sequence"]
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        table = "conversation_run_steers"
        quote = bind.dialect.identifier_preparer.quote
        for statement in create_policy_sql(quote, table_policy(quote, table)):
            op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, [table]):
                op.execute(sa.text(statement))
        role = app_role(bind)
        if role is not None:
            op.execute(
                sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {quote(table)} TO {quote(role)}")
            )


def downgrade() -> None:
    op.drop_table("conversation_run_steers")
