"""Workflow notification kinds: the notify step's ``workflow.notify`` (workflow-nodes-plan N6)
and ``workflow.schedule_paused`` (a schedule stopped after failed starts, starts plan 3.1).

Downgrade deletes those notifications and restores the earlier kind list.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i33_workflow_notify_kind"
down_revision = "i32_workflow_nodes"
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
    "approval.requested",
    "approval.expiring",
    "approval.decided",
    "emergency_stop.applied",
    "emergency_stop.released",
    "memory.consolidation_stopped",
)
_NEW_KINDS = ("workflow.notify", "workflow.schedule_paused")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ",".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint(
            "ck_notifications_kind", _in("kind", (*_KINDS_BEFORE, *_NEW_KINDS))
        )


def downgrade() -> None:
    for kind in _NEW_KINDS:
        op.execute(sa.text("DELETE FROM notifications WHERE kind = :kind").bindparams(kind=kind))
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint("ck_notifications_kind", _in("kind", _KINDS_BEFORE))
