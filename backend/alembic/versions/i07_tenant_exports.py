"""I-07 (plan S04, 9.7): company export jobs, purge receipts and purge reachability.

* ``tenant_exports``: one row per operator export; at most one queued/running job per
  company (partial unique index).
* ``tenant_purges``: purge receipts that outlive the company row (no FK).
* ``marketplace_installations.item_id``/``version_id`` nullable + install status
  ``source_removed``: another company's installation of a purged company's item keeps
  its local copy.
* ``agent_trigger_runs.tenant_id`` / ``agent_api_runs.tenant_id`` (backfilled from the
  trigger/deployment, then agent/conversation) so runs whose parent is gone are still
  the company's rows.

The deletion schedule reuses ``tenants.status = 'deletion_scheduled'`` and
``tenants.deletion_due_at``. Downgrade restores the previous schema (it refuses while a
``source_removed`` installation exists); export archives on disk are left in place.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i07_tenant_exports"
down_revision = "h04_authz_reconcile_runs"
branch_labels = None
depends_on = None

_TABLE = "tenant_exports"
_ACTIVE = "status IN ('queued','running')"
_INSTALL = "marketplace_installations"
_STATUS = "install_status IN ('active','needs_setup','disabled','uninstalled'{extra})"
_RUN_BACKFILL = {
    "agent_trigger_runs": (
        ("agent_triggers", "trigger_id"),
        ("agents", "agent_id"),
        ("conversations", "conversation_id"),
    ),
    "agent_api_runs": (
        ("agent_deployments", "deployment_id"),
        ("conversations", "conversation_id"),
    ),
}


def _run_tenants() -> None:
    for table, sources in _RUN_BACKFILL.items():
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("tenant_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
            )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        for parent, column in sources:
            op.execute(
                f"UPDATE {table} SET tenant_id = (SELECT p.tenant_id FROM {parent} p "  # noqa: S608
                f"WHERE p.id = {table}.{column}) WHERE tenant_id IS NULL AND {column} IS NOT NULL"
            )


def _installations(*, nullable: bool) -> None:
    extra = ",'source_removed'" if nullable else ""
    with op.batch_alter_table(_INSTALL) as batch:
        batch.drop_constraint("ck_marketplace_install_status", type_="check")
        batch.alter_column("item_id", existing_type=sa.Uuid(), nullable=nullable)
        batch.alter_column("version_id", existing_type=sa.Uuid(), nullable=nullable)
        batch.create_check_constraint("ck_marketplace_install_status", _STATUS.format(extra=extra))


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=f"fk_{_TABLE}_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=f"fk_{_TABLE}_requested_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')", name=f"ck_{_TABLE}_status"
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name=f"ck_{_TABLE}_progress"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(f"ix_{_TABLE}_tenant_created", _TABLE, ["tenant_id", "created_at"])
    op.create_index(
        f"uq_{_TABLE}_active",
        _TABLE,
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
        sqlite_where=sa.text(_ACTIVE),
    )
    op.create_table(
        "tenant_purges",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="rows_deleted", nullable=False),
        sa.Column("cleanup", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('rows_deleted','completed')", name="ck_tenant_purges_status"
        ),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant_purges"),
    )
    _installations(nullable=True)
    _run_tenants()


def downgrade() -> None:
    bind = op.get_bind()
    removed = bind.execute(
        sa.text(f"SELECT count(*) FROM {_INSTALL} WHERE install_status = 'source_removed'")
    ).scalar()
    if removed:
        raise RuntimeError("source_removed installations exist; delete them before downgrading")
    for table in _RUN_BACKFILL:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_tenant_id", type_="foreignkey")
            batch.drop_column("tenant_id")
    _installations(nullable=False)
    op.drop_table("tenant_purges")
    op.drop_index(f"uq_{_TABLE}_active", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_tenant_created", table_name=_TABLE)
    op.drop_table(_TABLE)
