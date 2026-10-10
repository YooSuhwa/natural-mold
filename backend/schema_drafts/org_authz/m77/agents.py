"""Frozen m77 agents fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agents",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("name", sa.String(length=100), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, primary_key=False),
        sa.Column(
            "runtime_name", sa.String(length=40), nullable=False, primary_key=False, unique=True
        ),
        sa.Column(
            "identity_mode",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="fixed",
            server_default="fixed",
        ),
        sa.Column("model_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("llm_credential_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="active"
        ),
        sa.Column(
            "runtime_profile",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="standard",
            server_default="standard",
        ),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column("model_params", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("middleware_configs", sa.JSON(), nullable=True, primary_key=False, default=list),
        sa.Column("runtime_policy", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("opener_questions", sa.JSON(), nullable=True, primary_key=False, default=list),
        sa.Column("model_fallback_list", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("image_path", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("template_id", sa.Uuid(), nullable=True, primary_key=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], name=None),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], name=None),
        sa.ForeignKeyConstraint(
            ["llm_credential_id"], ["credentials.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
