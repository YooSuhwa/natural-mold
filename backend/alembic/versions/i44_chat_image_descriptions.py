"""Pin attached image bytes and record authorized auxiliary model calls."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i44_chat_image_descriptions"
down_revision = "i39_skill_approval_default"
branch_labels = None
depends_on = None

_TABLE = "chat_image_descriptions"


def upgrade() -> None:
    op.add_column("message_attachments", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attachment_id",
            sa.Uuid(),
            sa.ForeignKey("message_attachments.id", ondelete="CASCADE"),
            nullable=False,
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
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("question_sha256", sa.String(64), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("description", sa.String(8000), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("credential_scope", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "attachment_id",
            "conversation_id",
            "content_sha256",
            "question_sha256",
            "model_id",
            "policy_sha256",
            name="uq_chat_image_description_request",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND description IS NULL AND failure_code IS NULL) OR "
            "(status = 'completed' AND length(description) BETWEEN 1 AND 8000 "
            "AND description IS NOT NULL AND failure_code IS NULL) OR "
            "(status = 'failed' AND description IS NULL AND failure_code IS NOT NULL)",
            name="ck_chat_image_description_result",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64 AND length(question_sha256) = 64 "
            "AND length(policy_sha256) = 64",
            name="ck_chat_image_description_hashes",
        ),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0) AND "
            "(estimated_cost_usd IS NULL OR estimated_cost_usd >= 0) AND "
            "(credential_scope IS NULL OR credential_scope IN ('personal', 'tenant'))",
            name="ck_chat_image_description_usage",
        ),
    )
    for column in ("conversation_id", "run_id"):
        op.create_index(f"ix_{_TABLE}_{column}", _TABLE, [column])
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
    op.drop_column("message_attachments", "content_sha256")
