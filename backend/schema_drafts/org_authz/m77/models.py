"""Frozen m77 models fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "models",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("provider", sa.String(length=50), nullable=False, primary_key=False),
        sa.Column("model_name", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("display_name", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("base_url", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column(
            "is_visible",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=True,
            server_default="true",
        ),
        sa.Column("default_credential_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "cost_per_input_token",
            sa.Numeric(precision=12, scale=8),
            nullable=True,
            primary_key=False,
        ),
        sa.Column(
            "cost_per_output_token",
            sa.Numeric(precision=12, scale=8),
            nullable=True,
            primary_key=False,
        ),
        sa.Column("context_window", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("input_modalities", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("output_modalities", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("supports_vision", sa.Boolean(), nullable=True, primary_key=False),
        sa.Column("supports_function_calling", sa.Boolean(), nullable=True, primary_key=False),
        sa.Column("supports_reasoning", sa.Boolean(), nullable=True, primary_key=False),
        sa.Column("source", sa.String(length=20), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(
            ["default_credential_id"], ["credentials.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
