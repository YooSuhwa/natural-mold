"""H-02 (plan m88 ``m88_requests_notifications``, 6.1, 14.2, S25): notifications.

* ``notifications``: one row per recipient; ``org_id`` NULL marks a company-level row.
  Index ``(user_id, read_at, created_at)`` serves the bell (6.1) and
  ``(user_id, kind, target_id)`` the 1-hour merge of 14.2.
* The kind list is a CHECK constraint (14.2 table).

Downgrade drops the table (notifications are not migrated anywhere).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "h02_notifications"
down_revision = "h03_audit_tenant_index"
branch_labels = None
depends_on = None

_TABLE = "notifications"
_KINDS = (
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


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{_TABLE}_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{_TABLE}_tenant_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=f"fk_{_TABLE}_user_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=f"fk_{_TABLE}_actor_id", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "kind IN (" + ",".join(f"'{kind}'" for kind in _KINDS) + ")",
            name=f"ck_{_TABLE}_kind",
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(f"ix_{_TABLE}_user_read_created", _TABLE, ["user_id", "read_at", "created_at"])
    op.create_index(f"ix_{_TABLE}_user_kind_target", _TABLE, ["user_id", "kind", "target_id"])


def downgrade() -> None:
    op.drop_table(_TABLE)
