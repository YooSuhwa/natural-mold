"""Memory consolidation (feature plan v2 순번 15, memory-policy-plan 5.6, PR-B3).

* ``memory_consolidation_runs`` (company table, RLS ``DIRECT_TENANT``): one row per
  consolidation run of a person's scope (counts and outcome, never memory text).
* ``memory_proposals`` gains ``profile_change`` (a proposed profile document line) and the
  ``revoke`` and ``profile`` actions.
* ``token_usages.conversation_id`` becomes nullable: a background job's model call has no
  conversation (``invoker_type = 'job'``).
* Notifications gain ``memory.consolidation_stopped``.

Downgrade drops the table and the column, deletes job usage rows and the new
notifications, and narrows the checks again.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i24_memory_consolidation"
down_revision = "i23_model_kind"
branch_labels = None
depends_on = None

_TABLE = "memory_consolidation_runs"
_ACTIONS_BEFORE = ("create", "update", "supersede", "merge", "pin", "expire")
_ACTIONS_AFTER = (*_ACTIONS_BEFORE, "revoke", "profile")
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
)
_NEW_KINDS = ("memory.consolidation_stopped",)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _action_check_exists() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        check.get("name") == "ck_memory_proposals_action"
        for check in inspector.get_check_constraints("memory_proposals")
    )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("proposed", sa.Integer(), nullable=False),
        sa.Column("applied", sa.Integer(), nullable=False),
        sa.Column("dropped", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{_TABLE}_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{_TABLE}_org_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=f"fk_{_TABLE}_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=f"fk_{_TABLE}_agent_id_agents", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            _in("outcome", ("ok", "failed", "stopped")), name=f"ck_{_TABLE}_outcome"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(f"ix_{_TABLE}_scope", _TABLE, ["user_id", "agent_id", "created_at"])

    with op.batch_alter_table("memory_proposals") as batch:
        batch.add_column(sa.Column("profile_change", sa.JSON(), nullable=True))
        if _action_check_exists():
            batch.drop_constraint("ck_memory_proposals_action", type_="check")
        batch.create_check_constraint("ck_memory_proposals_action", _in("action", _ACTIONS_AFTER))
    with op.batch_alter_table("token_usages") as batch:
        batch.alter_column("conversation_id", existing_type=sa.Uuid(), nullable=True)
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint(
            "ck_notifications_kind", _in("kind", (*_KINDS_BEFORE, *_NEW_KINDS))
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
        batch.create_check_constraint("ck_notifications_kind", _in("kind", _KINDS_BEFORE))
    op.execute(sa.text("DELETE FROM token_usages WHERE conversation_id IS NULL"))
    with op.batch_alter_table("token_usages") as batch:
        batch.alter_column("conversation_id", existing_type=sa.Uuid(), nullable=False)
    op.execute(sa.text("DELETE FROM memory_proposals WHERE action IN ('revoke', 'profile')"))
    with op.batch_alter_table("memory_proposals") as batch:
        batch.drop_constraint("ck_memory_proposals_action", type_="check")
        batch.create_check_constraint("ck_memory_proposals_action", _in("action", _ACTIONS_BEFORE))
        batch.drop_column("profile_change")
    op.drop_index(f"ix_{_TABLE}_scope", table_name=_TABLE)
    op.drop_table(_TABLE)
