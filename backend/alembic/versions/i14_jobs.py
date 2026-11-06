"""Generic background jobs (feature plan v2 순번 5, 공통 기반).

``jobs``: company and organization scoped rows (RLS ``DIRECT_TENANT``) claimed by the
separate worker process with ``FOR UPDATE SKIP LOCKED``. ``job_workers``: one platform row
per running worker process and its heartbeat (``PLATFORM_ONLY``). Downgrade drops both.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i14_jobs"
down_revision = "i13_guardrail_templates"
branch_labels = None
depends_on = None

_TABLES = ("jobs", "job_workers")


def _at(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_note", sa.String(length=200), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("locked_by", sa.String(length=80), nullable=True),
        _at("lease_until"),
        _at("heartbeat_at"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        _at("run_after", nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        _at("finished_at"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name="fk_jobs_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name="fk_jobs_actor_id_users", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','canceled')", name="ck_jobs_state"
        ),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="ck_jobs_progress"),
        sa.UniqueConstraint("tenant_id", "type", "idempotency_key", name="uq_jobs_idempotency"),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_claim", "jobs", ["state", "run_after"])
    op.create_index("ix_jobs_tenant_resource", "jobs", ["tenant_id", "type", "resource_id"])
    op.create_table(
        "job_workers",
        sa.Column("worker_id", sa.String(length=80), nullable=False),
        sa.Column("hostname", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("job_types", sa.JSON(), nullable=False),
        _at("started_at", nullable=False),
        _at("heartbeat_at", nullable=False),
        sa.PrimaryKeyConstraint("worker_id", name="pk_job_workers"),
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    quote = bind.dialect.identifier_preparer.quote
    for table in _TABLES:
        for statement in create_policy_sql(quote, table_policy(quote, table)):
            op.execute(sa.text(statement))
    if settings.db_rls_enabled:
        for statement in enable_sql(quote, list(_TABLES)):
            op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table("job_workers")
    op.drop_table("jobs")
