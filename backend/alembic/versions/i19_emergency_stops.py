"""Emergency stops (feature plan v2 순번 13, 17.4; app/approvals/emergency.py).

``emergency_stops``: one company row (RLS ``DIRECT_TENANT``) per stop of an agent, of one
person's agents or of one MCP server, at most one active per target. Two notification kinds
tell the owners a stop was applied or released. Downgrade drops both.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i19_emergency_stops"
down_revision = "i18_trigger_approval_policy"
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
)
_NEW_KINDS = ("emergency_stop.applied", "emergency_stop.released")
_TABLE = "emergency_stops"


def _kind_check(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ")"


def _at(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("scope_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("stopped_by", sa.Uuid(), nullable=True),
        _at("stopped_at", nullable=False),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        _at("released_at"),
        sa.Column("release_reason", sa.String(length=500), nullable=True),
        sa.Column("effects", sa.JSON(), nullable=False),
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
            ["stopped_by"], ["users.id"], name=f"fk_{_TABLE}_stopped_by_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["released_by"],
            ["users.id"],
            name=f"fk_{_TABLE}_released_by_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "scope_type IN ('agent', 'user', 'mcp_server')", name=f"ck_{_TABLE}_scope"
        ),
        sa.CheckConstraint("status IN ('active', 'released')", name=f"ck_{_TABLE}_status"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(
        f"uq_{_TABLE}_active_scope",
        _TABLE,
        ["tenant_id", "scope_type", "scope_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
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
