"""Frozen m77 credential_defaults fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "credential_defaults",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("scope_kind", sa.String(length=40), nullable=False, primary_key=False),
        sa.Column("scope_key", sa.String(length=120), nullable=False, primary_key=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "uq_credential_defaults_user_scope",
        metadata.tables["credential_defaults"].c["user_id"],
        metadata.tables["credential_defaults"].c["scope_kind"],
        metadata.tables["credential_defaults"].c["scope_key"],
        unique=True,
    )
