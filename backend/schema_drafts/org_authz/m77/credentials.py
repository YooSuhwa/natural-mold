"""Frozen m77 credentials fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "credentials",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("definition_key", sa.String(length=80), nullable=False, primary_key=False),
        sa.Column("name", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("data_encrypted", sa.Text(), nullable=False, primary_key=False),
        sa.Column("key_id", sa.String(length=16), nullable=False, primary_key=False),
        sa.Column("field_keys", sa.JSON(), nullable=True, primary_key=False, default=list),
        sa.Column("is_shared", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=False,
            server_default="false",
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="active"
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("last_test_result", sa.JSON(), nullable=True, primary_key=False),
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
            "(is_system = false) OR (user_id IS NULL)", name="ck_credentials_system_user_null"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
