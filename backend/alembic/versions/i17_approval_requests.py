"""Approval requests (feature plan v2 순번 13, chapter 17.2; app/approvals).

``approval_requests``: one company row (RLS ``DIRECT_TENANT``) per approval interrupt of a
run, decided once from the chat card or the approval inbox. Three notification kinds tell
the decider about a new request, one about to expire and the decision. Downgrade drops
the table and the kinds (rows of those kinds first).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i17_approval_requests"
down_revision = "i16_mcp_tool_review"
branch_labels = None
depends_on = None

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
)
_NEW_KINDS = ("approval.requested", "approval.expiring", "approval.decided")
_TABLE = "approval_requests"


def _kind_check(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ",".join(f"'{value}'" for value in values) + ")"


def _user(name: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [name], ["users.id"], name=f"fk_{_TABLE}_{name}_users", ondelete=ondelete
    )


def _at(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("interrupt_id", sa.String(length=200), nullable=False),
        sa.Column("namespace", sa.JSON(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("agent_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("trigger_id", sa.Uuid(), nullable=True),
        sa.Column("trigger_run_id", sa.Uuid(), nullable=True),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=True),
        sa.Column("decider_user_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("args_digest", sa.String(length=64), nullable=False),
        sa.Column("edited_args_digest", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("policy_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("channel", sa.String(length=20), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("decision_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decisions", sa.JSON(), nullable=True),
        sa.Column("resumed_run_id", sa.Uuid(), nullable=True),
        _at("expires_at", nullable=False),
        _at("expiring_notified_at"),
        _at("decided_at"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{_TABLE}_tenant_id_tenants", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=f"fk_{_TABLE}_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["conversation_runs.id"],
            name=f"fk_{_TABLE}_run_id_conversation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resumed_run_id"],
            ["conversation_runs.id"],
            name=f"fk_{_TABLE}_resumed_run_id_conversation_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=f"fk_{_TABLE}_agent_id_agents", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["agent_triggers.id"],
            name=f"fk_{_TABLE}_trigger_id_agent_triggers",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_run_id"],
            ["agent_trigger_runs.id"],
            name=f"fk_{_TABLE}_trigger_run_id_agent_trigger_runs",
            ondelete="SET NULL",
        ),
        _user("subject_user_id", "CASCADE"),
        _user("requester_user_id", "SET NULL"),
        _user("decider_user_id", "CASCADE"),
        _user("decided_by", "SET NULL"),
        sa.CheckConstraint(
            _in("status", ("pending", "approved", "edited", "rejected", "expired", "canceled")),
            name=f"ck_{_TABLE}_status",
        ),
        sa.CheckConstraint(
            _in("source", ("chat", "trigger", "api", "subagent")), name=f"ck_{_TABLE}_source"
        ),
        sa.CheckConstraint(
            "channel IS NULL OR " + _in("channel", ("chat", "inbox", "expiry", "emergency_stop")),
            name=f"ck_{_TABLE}_channel",
        ),
        sa.UniqueConstraint("run_id", "interrupt_id", name=f"uq_{_TABLE}_run_interrupt"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(f"ix_{_TABLE}_decider_status", _TABLE, ["decider_user_id", "status"])
    op.create_index(f"ix_{_TABLE}_status_expires", _TABLE, ["status", "expires_at"])
    op.create_index(f"ix_{_TABLE}_conversation", _TABLE, ["conversation_id"])
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint(
            "ck_notifications_kind", _kind_check((*_KINDS_BEFORE, *_NEW_KINDS))
        )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    quote = bind.dialect.identifier_preparer.quote
    for statement in create_policy_sql(quote, table_policy(quote, _TABLE)):
        op.execute(sa.text(statement))
    if settings.db_rls_enabled:
        for statement in enable_sql(quote, [_TABLE]):
            op.execute(sa.text(statement))


def downgrade() -> None:
    for kind in _NEW_KINDS:
        op.execute(sa.text("DELETE FROM notifications WHERE kind = :kind").bindparams(kind=kind))
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint("ck_notifications_kind", _kind_check(_KINDS_BEFORE))
    op.drop_table(_TABLE)
