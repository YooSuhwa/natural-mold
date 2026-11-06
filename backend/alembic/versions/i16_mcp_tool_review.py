"""MCP tool review (feature plan v2 순번 10, decision D11; app/mcp/review.py).

``mcp_tools`` gains the review columns and the stored annotations. Every existing tool is
``approved`` with no fingerprint: it was already in use, and the first check adopts what it
serves. Only tools discovered from now on start ``pending``. The notification kind
``mcp_tool.review_needed`` tells server managers a tool waits for them. Downgrade drops the
columns and the kind (rows of that kind first).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i16_mcp_tool_review"
down_revision = "i15_pgvector"
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
)
_NEW_KIND = "mcp_tool.review_needed"


def _kind_check(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")"


def upgrade() -> None:
    with op.batch_alter_table("mcp_tools") as batch:
        batch.add_column(sa.Column("annotations_json", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("review_state", sa.String(16), nullable=False, server_default="approved")
        )
        batch.add_column(sa.Column("approved_fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("pending_fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("pending_definition", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("rejected_fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reviewed_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_mcp_tools_reviewed_by_users", "users", ["reviewed_by"], ["id"], ondelete="SET NULL"
        )
        batch.create_check_constraint(
            "ck_mcp_tools_review_state", "review_state IN ('pending', 'approved', 'rejected')"
        )
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint(
            "ck_notifications_kind", _kind_check((*_KINDS_BEFORE, _NEW_KIND))
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notifications WHERE kind = :kind").bindparams(kind=_NEW_KIND))
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint("ck_notifications_kind", _kind_check(_KINDS_BEFORE))
    with op.batch_alter_table("mcp_tools") as batch:
        batch.drop_constraint("ck_mcp_tools_review_state", type_="check")
        batch.drop_constraint("fk_mcp_tools_reviewed_by_users", type_="foreignkey")
        for column in (
            "reviewed_by",
            "reviewed_at",
            "rejected_fingerprint",
            "pending_definition",
            "pending_fingerprint",
            "approved_fingerprint",
            "review_state",
            "annotations_json",
        ):
            batch.drop_column(column)
