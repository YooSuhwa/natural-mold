"""H-04 (plan 14.3, S27; chained after h08): ``authz_reconcile_runs`` for the operator sync screen.

One row per reconcile run (operator or daily job). ``active`` is TRUE while queued or
running, NULL afterwards; its unique constraint admits one live run at a time.
``report`` keeps counts only. Downgrade drops the table (run history only).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "h04_authz_reconcile_runs"
down_revision = "h08_sso_hardening"
branch_labels = None
depends_on = None

_TABLE = "authz_reconcile_runs"
_STATUSES = ("queued", "running", "succeeded", "failed", "skipped")
_TRIGGERS = ("manual", "scheduled")


def _in(column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    allowed = ",".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{_TABLE}_{column}")


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("partitions_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partitions_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=f"fk_{_TABLE}_requested_by",
            ondelete="SET NULL",
        ),
        _in("status", _STATUSES),
        _in("trigger", _TRIGGERS),
        sa.CheckConstraint("active IS NULL OR active", name=f"ck_{_TABLE}_active"),
        sa.UniqueConstraint("active", name=f"uq_{_TABLE}_active"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(f"ix_{_TABLE}_created_at", _TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
