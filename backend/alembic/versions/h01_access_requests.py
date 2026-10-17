"""H-01 (plan m88 ``m88_requests_notifications``, 6.1, 10.9, S18): access requests.

* ``access_requests``: a member's request for a higher level on a resource of the
  organization; one pending row per (resource, requester, level) via a partial unique
  index. ``expires_at`` (created + 30 days) drives lazy expiry.
* ``notifications`` of the same plan row belong to H-02 and are not created here.

Downgrade drops the table (requests are not migrated anywhere).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "h01_access_requests"
down_revision = "f08_trigger_run_reason_code"
branch_labels = None
depends_on = None

_TABLE = "access_requests"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=20), nullable=False),
        sa.Column("requester_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("granted_relation", sa.String(length=20), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{_TABLE}_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"], ["users.id"], name=f"fk_{_TABLE}_requester_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=f"fk_{_TABLE}_decided_by", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','denied','cancelled','expired')",
            name=f"ck_{_TABLE}_status",
        ),
        sa.CheckConstraint(
            "resource_type IN ('agent','skill','tool','mcp_server')",
            name=f"ck_{_TABLE}_resource_type",
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(
        f"uq_{_TABLE}_pending",
        _TABLE,
        ["org_id", "resource_type", "resource_id", "requester_id", "relation"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_index(f"ix_{_TABLE}_org_status_created", _TABLE, ["org_id", "status", "created_at"])
    op.create_index(f"ix_{_TABLE}_requester_created", _TABLE, ["requester_id", "created_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
