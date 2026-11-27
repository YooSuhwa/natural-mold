"""Store recoverable model-send reservations on the existing private run."""

import sqlalchemy as sa

from alembic import op

revision = "i50_chat_model_attempts"
down_revision = "i49_independent_chat_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No backfill: an older worker may already have sent a request.
    op.add_column(
        "conversation_runs", sa.Column("recovery_model_attempts", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("conversation_runs", "recovery_model_attempts")
