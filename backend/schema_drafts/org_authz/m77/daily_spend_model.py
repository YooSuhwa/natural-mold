"""Frozen m77 daily_spend_model fixture definition; do not derive from live ORM."""

from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "daily_spend_model",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("date", sa.Date(), nullable=False, primary_key=False),
        sa.Column("model_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("total_tokens_in", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("total_tokens_out", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(precision=20, scale=8),
            nullable=False,
            primary_key=False,
            default=Decimal("0"),
        ),
        sa.Column("request_count", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("date", "model_id", name="uq_daily_spend_model_date_model"),
    )
    sa.Index(
        "ix_daily_spend_model_date", metadata.tables["daily_spend_model"].c["date"], unique=False
    )
    sa.Index(
        "ix_daily_spend_model_model_date",
        metadata.tables["daily_spend_model"].c["model_id"],
        metadata.tables["daily_spend_model"].c["date"],
        unique=False,
    )
