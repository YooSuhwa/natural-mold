"""Memory policy (feature plan v2 순번 15, memory-policy-plan 5.1, PR-B1).

``memory_records``/``memory_proposals`` gain what a memory is (kind, subject, topic,
occurrence and validity), where it came from (origin, sensitivity, flags) and who approved
or revoked it. Existing rows become ``origin='legacy'`` (Q7). Three company tables
(RLS ``DIRECT_TENANT``): ``memory_revisions`` (before/after per change, removed with the
memory), ``memory_sources`` (a derived memory's evidence) and ``memory_erasures`` (a
deleted memory's keyed digest, so the same text is not written again).
``user_memory_settings`` gains the approval mode (5.11) and the consolidation switch;
``conversations.memory_external_content_at`` marks when outside content first entered a
conversation (5.3-1).

Downgrade drops the three tables and the new columns; erased digests and revisions are lost.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i21_memory_policy"
down_revision = "i20_deployment_approval_policy"
branch_labels = None
depends_on = None

_TABLES = ("memory_revisions", "memory_sources", "memory_erasures")
_STATUSES = ("active", "superseded", "revoked", "deleted")
_KINDS = ("fact", "preference", "commitment", "experience", "episode", "procedure")
_ORIGINS = ("user_stated", "agent_inferred", "legacy", "consolidation", "tool_derived")
_SENSITIVITIES = ("normal", "contact", "sensitive")
_REVISION_ACTIONS = (
    "create",
    "update",
    "supersede",
    "revoke",
    "restore",
    "approve",
    "pin",
    "unpin",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _shape_columns() -> list[sa.Column]:
    """Columns shared by records and proposals (what the memory is, where it came from)."""
    return [
        sa.Column("kind", sa.String(20), nullable=False, server_default="fact"),
        sa.Column("subject", sa.String(10), nullable=False, server_default="self"),
        sa.Column("subject_label", sa.String(120), nullable=True),
        sa.Column("topic", sa.String(120), nullable=True),
        sa.Column("origin", sa.String(20), nullable=False, server_default="legacy"),
        sa.Column("sensitivity", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("flags", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("occurred_start", sa.DateTime(), nullable=True),
        sa.Column("occurred_end", sa.DateTime(), nullable=True),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
    ]


def _scope(table: str) -> list[sa.Column | sa.Constraint]:
    return [
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{table}_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{table}_org_tenant",
            ondelete="RESTRICT",
        ),
    ]


def _user_fk(table: str, column: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=f"fk_{table}_{column}_users", ondelete=ondelete
    )


def _record_fk(table: str, column: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        ["memory_records.id"],
        name=f"fk_{table}_{column}_memory_records",
        ondelete=ondelete,
    )


def upgrade() -> None:
    with op.batch_alter_table("memory_records") as batch:
        for column in _shape_columns():
            batch.add_column(column)
        batch.add_column(sa.Column("title", sa.String(200), nullable=True))
        batch.add_column(
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("importance", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("episode_ref", sa.String(128), nullable=True))
        batch.add_column(sa.Column("superseded_by_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("approved_by", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("approval_channel", sa.String(20), nullable=True))
        batch.add_column(sa.Column("revoked_by", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("revoked_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("revoke_reason", sa.String(200), nullable=True))
        batch.add_column(sa.Column("last_recalled_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column("recall_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_foreign_key(
            "fk_memory_records_superseded_by_id_memory_records",
            "memory_records",
            ["superseded_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_memory_records_approved_by_users",
            "users",
            ["approved_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_memory_records_revoked_by_users",
            "users",
            ["revoked_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.drop_constraint("ck_memory_records_status", type_="check")  # m56: active/deleted
        batch.create_check_constraint("ck_memory_records_status", _in("status", _STATUSES))
        batch.create_check_constraint("ck_memory_records_kind", _in("kind", _KINDS))
        batch.create_check_constraint(
            "ck_memory_records_subject", _in("subject", ("self", "other"))
        )
        batch.create_check_constraint("ck_memory_records_origin", _in("origin", _ORIGINS))
        batch.create_check_constraint(
            "ck_memory_records_sensitivity", _in("sensitivity", _SENSITIVITIES)
        )
    op.create_index("ix_memory_records_topic", "memory_records", ["topic"])

    with op.batch_alter_table("memory_proposals") as batch:
        for column in _shape_columns():
            batch.add_column(column)
        batch.add_column(
            sa.Column("action", sa.String(20), nullable=False, server_default="create")
        )
        batch.add_column(sa.Column("target_record_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("record_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("source_ids", sa.JSON(), nullable=True))
        batch.create_foreign_key(
            "fk_memory_proposals_target_record_id_memory_records",
            "memory_records",
            ["target_record_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_memory_proposals_record_id_memory_records",
            "memory_records",
            ["record_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("user_memory_settings") as batch:
        batch.add_column(sa.Column("approval_mode", sa.String(20), nullable=True))
        batch.add_column(sa.Column("approval_axes", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column(
                "consolidation_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )

    with op.batch_alter_table("conversations") as batch:
        batch.add_column(
            sa.Column("memory_external_content_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        "memory_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scope("memory_revisions"),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("run_path", sa.String(20), nullable=True),
        sa.Column("before_content", sa.Text(), nullable=True),
        sa.Column("after_content", sa.Text(), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _record_fk("memory_revisions", "record_id", "CASCADE"),
        _user_fk("memory_revisions", "user_id", "CASCADE"),
        _user_fk("memory_revisions", "actor_user_id", "SET NULL"),
        sa.CheckConstraint(_in("action", _REVISION_ACTIONS), name="ck_memory_revisions_action"),
        sa.PrimaryKeyConstraint("id", name="pk_memory_revisions"),
    )
    op.create_index("ix_memory_revisions_record", "memory_revisions", ["record_id", "created_at"])
    op.create_table(
        "memory_sources",
        *_scope("memory_sources"),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _record_fk("memory_sources", "record_id", "CASCADE"),
        _record_fk("memory_sources", "source_record_id", "CASCADE"),
        sa.PrimaryKeyConstraint("record_id", "source_record_id", name="pk_memory_sources"),
    )
    op.create_index("ix_memory_sources_source", "memory_sources", ["source_record_id"])
    op.create_table(
        "memory_erasures",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scope("memory_erasures"),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _user_fk("memory_erasures", "user_id", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_memory_erasures"),
    )
    op.create_index(
        "ix_memory_erasures_user_digest", "memory_erasures", ["user_id", "content_digest"]
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
    for table in reversed(_TABLES):
        op.drop_table(table)
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("memory_external_content_at")
    with op.batch_alter_table("user_memory_settings") as batch:
        for column in ("consolidation_enabled", "approval_axes", "approval_mode"):
            batch.drop_column(column)
    with op.batch_alter_table("memory_proposals") as batch:
        batch.drop_constraint("fk_memory_proposals_record_id_memory_records", type_="foreignkey")
        batch.drop_constraint(
            "fk_memory_proposals_target_record_id_memory_records", type_="foreignkey"
        )
        for column in (
            "source_ids",
            "record_id",
            "target_record_id",
            "action",
            *(column.name for column in _shape_columns()),
        ):
            batch.drop_column(column)
    op.drop_index("ix_memory_records_topic", table_name="memory_records")
    # m56 knows only active/deleted: superseded and revoked rows stop being served.
    op.execute(
        sa.text(
            "UPDATE memory_records SET status = 'deleted' WHERE status IN ('superseded', 'revoked')"
        )
    )
    with op.batch_alter_table("memory_records") as batch:
        for constraint in (
            "ck_memory_records_sensitivity",
            "ck_memory_records_origin",
            "ck_memory_records_subject",
            "ck_memory_records_kind",
            "ck_memory_records_status",
        ):
            batch.drop_constraint(constraint, type_="check")
        batch.create_check_constraint("ck_memory_records_status", "status in ('active', 'deleted')")
        for fk in (
            "fk_memory_records_revoked_by_users",
            "fk_memory_records_approved_by_users",
            "fk_memory_records_superseded_by_id_memory_records",
        ):
            batch.drop_constraint(fk, type_="foreignkey")
        for column in (
            "recall_count",
            "last_recalled_at",
            "revoke_reason",
            "revoked_at",
            "revoked_by",
            "approval_channel",
            "approved_at",
            "approved_by",
            "superseded_by_id",
            "episode_ref",
            "importance",
            "pinned",
            "title",
            *(column.name for column in _shape_columns()),
        ):
            batch.drop_column(column)
