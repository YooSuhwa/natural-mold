"""Deliver chat work notifications with durable, scoped idempotency receipts."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i47_chat_notifications"
down_revision = "i45_goal_sandboxes"
branch_labels = None
depends_on = None

_TABLE = "chat_notification_outbox"
_KINDS_BEFORE = (
    "access_request.created",
    "access_request.decided",
    "grant.received",
    "trigger.blocked",
    "connection.owner_needed",
    "delegation.broken",
    "ownership.transferred",
    "org.invitation",
    "tenant.limit_warning",
    "tenant.suspended",
    "marketplace.publish_requested",
    "marketplace.publish_decided",
    "usage.limit_warning",
    "agent_memo.proposal",
    "mcp_tool.review_needed",
    "approval.requested",
    "approval.expiring",
    "approval.decided",
    "emergency_stop.applied",
    "emergency_stop.released",
    "memory.consolidation_stopped",
    "workflow.notify",
    "workflow.schedule_paused",
)
_NEW_KINDS = ("chat_goal.completed", "chat_goal.blocked", "chat_task.completed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ",".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint(
            "ck_notifications_kind", _in("kind", (*_KINDS_BEFORE, *_NEW_KINDS))
        )
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("goal_id", sa.Uuid()),
        sa.Column("goal_version", sa.Integer()),
        sa.Column("goal_revision", sa.Integer()),
        sa.Column("artifact_id", sa.Uuid()),
        sa.Column("version_id", sa.Uuid()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "approval_request_id",
            sa.Uuid(),
            sa.ForeignKey("approval_requests.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("approval_request_id", name="uq_chat_notice_approval_request"),
        sa.Column(
            "notification_id", sa.Uuid(), sa.ForeignKey("notifications.id", ondelete="SET NULL")
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name="fk_chat_notification_outbox_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "kind", "source_id", name="uq_chat_notice_recipient_source"),
        sa.CheckConstraint(
            "(kind = 'chat_task.completed' AND goal_id IS NULL AND goal_version IS NULL "
            "AND goal_revision IS NULL) OR (kind IN ('chat_goal.completed','chat_goal.blocked',"
            "'chat_goal.approval_requested') "
            "AND goal_id IS NOT NULL AND goal_version IS NOT NULL AND goal_revision IS NOT NULL "
            "AND goal_version > 0 AND goal_revision > 0)",
            name="ck_chat_notice_source",
        ),
        sa.CheckConstraint(
            "(artifact_id IS NULL) = (version_id IS NULL)", name="ck_chat_notice_artifact_version"
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND finished_at IS NULL AND notification_id IS NULL) OR "
            "(status IN ('delivered','suppressed') AND finished_at IS NOT NULL)",
            name="ck_chat_notice_delivery",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_chat_notice_attempts"),
    )
    op.create_index("ix_chat_notice_due", _TABLE, ["tenant_id", "status", "available_at", "id"])
    op.create_index(
        "ix_chat_notice_pending_tenants",
        _TABLE,
        ["available_at", "tenant_id"],
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        quote = bind.dialect.identifier_preparer.quote
        for statement in create_policy_sql(quote, table_policy(quote, _TABLE)):
            op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, [_TABLE]):
                op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table(_TABLE)
    for kind in _NEW_KINDS:
        op.execute(sa.text("DELETE FROM notifications WHERE kind = :kind").bindparams(kind=kind))
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint("ck_notifications_kind", _in("kind", _KINDS_BEFORE))
