"""Frozen m77 refresh_tokens fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "refresh_tokens",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False, index=True),
        sa.Column(
            "token_hash",
            sa.String(length=64),
            nullable=False,
            primary_key=False,
            unique=True,
            index=True,
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            default=defaults.now_aware,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False, primary_key=False, index=True
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("user_agent", sa.Text(), nullable=True, primary_key=False),
        sa.Column("ip", sa.String(length=45), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            default=defaults.now_aware,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"], ["refresh_tokens.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
