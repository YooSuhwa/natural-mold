"""M57: audit events.

Revision ID: m57_audit_events
Revises: m56_agent_api_deployments
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m57_audit_events"
down_revision = "m56_agent_api_deployments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_api_key_id", sa.Uuid(), nullable=True),
        sa.Column("actor_email_snapshot", sa.String(length=255), nullable=True),
        sa.Column("actor_label", sa.String(length=200), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("owner_email_snapshot", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("target_name_snapshot", sa.String(length=200), nullable=True),
        sa.Column("target_owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("reason_message", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=80), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["target_owner_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_owner_created",
        "audit_events",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_audit_events_actor_created",
        "audit_events",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_audit_events_action_created",
        "audit_events",
        ["action", "created_at"],
    )
    op.create_index("ix_audit_events_target", "audit_events", ["target_type", "target_id"])
    op.create_index(
        "ix_audit_events_outcome_created",
        "audit_events",
        ["outcome", "created_at"],
    )
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])
    op.create_index("ix_audit_events_trace_id", "audit_events", ["trace_id"])
    op.create_index("ix_audit_events_run_id", "audit_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_run_id", table_name="audit_events")
    op.drop_index("ix_audit_events_trace_id", table_name="audit_events")
    op.drop_index("ix_audit_events_request_id", table_name="audit_events")
    op.drop_index("ix_audit_events_outcome_created", table_name="audit_events")
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.drop_index("ix_audit_events_action_created", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_created", table_name="audit_events")
    op.drop_index("ix_audit_events_owner_created", table_name="audit_events")
    op.drop_table("audit_events")
