"""Frozen m77 credential_audit_logs fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "credential_audit_logs",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("credential_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("action", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("source", sa.String(length=20), nullable=False, primary_key=False, default="api"),
        sa.Column("ip", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("error", sa.Text(), nullable=True, primary_key=False),
        sa.Column("log_metadata", sa.JSON(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name=None, ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_credential_audit_logs_credential_created",
        metadata.tables["credential_audit_logs"].c["credential_id"],
        metadata.tables["credential_audit_logs"].c["created_at"],
        unique=False,
    )
