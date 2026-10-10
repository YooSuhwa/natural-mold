"""Frozen m77 health_check_history fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "health_check_history",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("target_kind", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("target_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("error_kind", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("error_message", sa.Text(), nullable=True, primary_key=False),
        sa.Column("raw_result", sa.JSON(), nullable=True, primary_key=False),
        sa.Column(
            "checked_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_health_check_history_target_checked_at",
        metadata.tables["health_check_history"].c["target_kind"],
        metadata.tables["health_check_history"].c["target_id"],
        metadata.tables["health_check_history"].c["checked_at"],
        unique=False,
    )
