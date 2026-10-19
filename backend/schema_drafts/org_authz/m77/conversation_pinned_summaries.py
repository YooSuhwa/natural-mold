"""Frozen m77 conversation_pinned_summaries fixture definition; do not derive from live ORM."""

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "conversation_pinned_summaries",
        metadata,
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("source_message_id", sa.String(length=255), nullable=False, primary_key=False),
        sa.Column(
            "source_branch_checkpoint_id", sa.String(length=64), nullable=False, primary_key=False
        ),
        sa.Column("snapshot_text", sa.Text(), nullable=False, primary_key=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.CheckConstraint(
            "length(snapshot_text) <= 4000",
            name="ck_conversation_pinned_summaries_snapshot_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("conversation_id", name=None),
    )
