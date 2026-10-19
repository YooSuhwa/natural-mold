"""Frozen m77 audit_events fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "audit_events",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("actor_type", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("actor_api_key_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("actor_email_snapshot", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("actor_label", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("owner_email_snapshot", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("action", sa.String(length=80), nullable=False, primary_key=False),
        sa.Column("target_type", sa.String(length=50), nullable=False, primary_key=False),
        sa.Column("target_id", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column("target_name_snapshot", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column("target_owner_user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("outcome", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("reason_message", sa.Text(), nullable=True, primary_key=False),
        sa.Column("request_id", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column("run_id", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("metadata", sa.JSON(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=None, ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["target_owner_user_id"], ["users.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name=None, ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_audit_events_action_created",
        metadata.tables["audit_events"].c["action"],
        metadata.tables["audit_events"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_audit_events_actor_created",
        metadata.tables["audit_events"].c["actor_user_id"],
        metadata.tables["audit_events"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_audit_events_outcome_created",
        metadata.tables["audit_events"].c["outcome"],
        metadata.tables["audit_events"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_audit_events_owner_created",
        metadata.tables["audit_events"].c["owner_user_id"],
        metadata.tables["audit_events"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_audit_events_request_id", metadata.tables["audit_events"].c["request_id"], unique=False
    )
    sa.Index("ix_audit_events_run_id", metadata.tables["audit_events"].c["run_id"], unique=False)
    sa.Index(
        "ix_audit_events_target",
        metadata.tables["audit_events"].c["target_type"],
        metadata.tables["audit_events"].c["target_id"],
        unique=False,
    )
    sa.Index(
        "ix_audit_events_trace_id", metadata.tables["audit_events"].c["trace_id"], unique=False
    )
