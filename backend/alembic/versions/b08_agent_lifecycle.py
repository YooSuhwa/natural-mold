"""Add retained Agent lifecycle history and purge preparation schema."""
# allow: SIZE_OK — self-contained Alembic DDL/data-backfill
# revision remains one stable migration unit for DDL/backfill symmetry.

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b08_agent_lifecycle"
down_revision = "m83_org_scope_not_null"
branch_labels = None
depends_on = None


class LifecycleDowngradeError(RuntimeError):
    """Raised when retained lifecycle data cannot be represented at m83."""


def _replace_fk(table: str, column: str, target: str, name: str, ondelete: str) -> None:
    matches = [
        fk
        for fk in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if fk["constrained_columns"] == [column]
    ]
    if len(matches) != 1 or matches[0]["referred_table"] != target:
        msg = f"Expected exactly one foreign key for {table}.{column} -> {target}.id"
        raise LifecycleDowngradeError(msg)
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(matches[0]["name"], type_="foreignkey")
        batch.create_foreign_key(name, target, [column], ["id"], ondelete=ondelete)


def _add_conversation_lifecycle() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("agent_lifecycle_state", sa.String(20), nullable=True))
        batch.add_column(sa.Column("agent_archived_at_snapshot", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("agent_purged_at", sa.DateTime(timezone=True)))
    op.execute(
        sa.text(
            "UPDATE conversations AS c SET "
            "agent_lifecycle_state = CASE "
            "WHEN c.agent_id IS NULL THEN 'purged' "
            "WHEN a.archived_at IS NOT NULL THEN 'archived' ELSE 'active' END, "
            "agent_archived_at_snapshot = a.archived_at, "
            "agent_purged_at = CASE WHEN c.agent_id IS NULL THEN "
            "COALESCE(c.last_activity_at, c.updated_at, c.created_at, CURRENT_TIMESTAMP) END "
            "FROM agents AS a WHERE c.agent_id = a.id"
        )
    )
    op.execute(
        sa.text(
            "UPDATE conversations SET agent_lifecycle_state = 'purged', "
            "agent_purged_at = COALESCE("
            "last_activity_at, updated_at, created_at, CURRENT_TIMESTAMP) "
            "WHERE agent_id IS NULL"
        )
    )
    with op.batch_alter_table("conversations") as batch:
        batch.alter_column(
            "agent_lifecycle_state",
            existing_type=sa.String(20),
            nullable=False,
            server_default="active",
        )
        batch.create_check_constraint(
            "ck_conversations_agent_lifecycle_state",
            "agent_lifecycle_state IN ('active', 'archived', 'purged')",
        )
        batch.create_check_constraint(
            "ck_conversations_agent_lifecycle_binding",
            "(agent_lifecycle_state IN ('active', 'archived') "
            "AND agent_id IS NOT NULL AND agent_purged_at IS NULL) OR "
            "(agent_lifecycle_state = 'purged' "
            "AND agent_id IS NULL AND agent_purged_at IS NOT NULL)",
        )


def _detach_retained_agent_references() -> None:
    specs = (
        ("conversation_runs", "fk_conversation_runs_agent_retained"),
        ("conversation_run_inputs", "fk_conversation_run_inputs_agent_retained"),
        ("conversation_artifacts", "fk_conversation_artifacts_agent_retained"),
        ("token_usages", "fk_token_usages_agent_retained"),
        ("mcp_app_invocation_bindings", "fk_mcp_app_bindings_agent_retained"),
    )
    for table, name in specs:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("agent_id", existing_type=sa.Uuid(), nullable=True)
        _replace_fk(table, "agent_id", "agents", name, "SET NULL")


def _add_envelope_history() -> None:
    for table in ("agent_api_threads", "agent_api_runs"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("deployment_public_id_snapshot", sa.String(80)))
        op.execute(
            sa.text(
                f"UPDATE {table} AS envelope SET deployment_public_id_snapshot = "
                "deployment.public_id FROM agent_deployments AS deployment "
                "WHERE envelope.deployment_id = deployment.id"
            )
        )
        with op.batch_alter_table(table) as batch:
            batch.alter_column("deployment_id", existing_type=sa.Uuid(), nullable=True)
            batch.alter_column(
                "deployment_public_id_snapshot", existing_type=sa.String(80), nullable=False
            )
        _replace_fk(
            table,
            "deployment_id",
            "agent_deployments",
            f"fk_{table}_deployment_retained",
            "SET NULL",
        )
    with op.batch_alter_table("agent_api_runs") as batch:
        batch.add_column(sa.Column("conversation_run_id", sa.Uuid()))
        batch.create_foreign_key(
            "fk_agent_api_runs_conversation_run",
            "conversation_runs",
            ["conversation_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_agent_api_runs_conversation_run", ["conversation_run_id"]
        )

    with op.batch_alter_table("agent_trigger_runs") as batch:
        batch.add_column(sa.Column("trigger_name_snapshot", sa.String(120)))
        batch.add_column(sa.Column("conversation_run_id", sa.Uuid()))
    op.execute(
        sa.text(
            "UPDATE agent_trigger_runs AS run SET trigger_name_snapshot = trigger.name "
            "FROM agent_triggers AS trigger WHERE run.trigger_id = trigger.id"
        )
    )
    with op.batch_alter_table("agent_trigger_runs") as batch:
        batch.alter_column("trigger_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("agent_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("trigger_name_snapshot", existing_type=sa.String(120), nullable=False)
        batch.create_foreign_key(
            "fk_agent_trigger_runs_conversation_run",
            "conversation_runs",
            ["conversation_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_agent_trigger_runs_conversation_run", ["conversation_run_id"]
        )
    _replace_fk(
        "agent_trigger_runs",
        "trigger_id",
        "agent_triggers",
        "fk_agent_trigger_runs_trigger_retained",
        "SET NULL",
    )
    _replace_fk(
        "agent_trigger_runs",
        "agent_id",
        "agents",
        "fk_agent_trigger_runs_agent_retained",
        "SET NULL",
    )


def _create_run_participants() -> None:
    op.create_table(
        "conversation_run_agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid()),
        sa.Column("agent_id_snapshot", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("path", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["conversation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "agent_id_snapshot", name="uq_conversation_run_agents_snapshot"
        ),
        sa.CheckConstraint("role IN ('root', 'subagent')", name="ck_conversation_run_agents_role"),
    )
    op.execute(
        sa.text(
            "INSERT INTO conversation_run_agents "
            "(id, run_id, agent_id, agent_id_snapshot, role, path, created_at) "
            "SELECT id, id, agent_id, agent_id, 'root', '[]'::json, created_at "
            "FROM conversation_runs WHERE agent_id IS NOT NULL"
        )
    )


def _create_history_tables() -> None:
    op.create_table(
        "conversation_history_manifests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("source_agent_id_snapshot", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("checkpoint_count", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("canonical_payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "schema_version", name="uq_conversation_history_manifests_version"
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_conversation_history_manifests_version"),
        sa.CheckConstraint(
            "state IN ('building', 'complete')", name="ck_conversation_history_manifests_state"
        ),
        sa.CheckConstraint(
            "checkpoint_count >= 0 AND event_count >= 0",
            name="ck_conversation_history_manifests_counts",
        ),
        sa.CheckConstraint(
            "char_length(canonical_payload_sha256) = 64",
            name="ck_conversation_history_manifests_checksum",
        ),
    )
    op.create_table(
        "conversation_history_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("manifest_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.String(200), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(200)),
        sa.Column("checkpoint_namespace", sa.String(200), nullable=False),
        sa.Column("channel_values", sa.JSON(), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("branch", sa.JSON(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["manifest_id"], ["conversation_history_manifests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manifest_id",
            "checkpoint_id",
            "checkpoint_namespace",
            name="uq_conversation_history_checkpoints_identity",
        ),
        sa.CheckConstraint(
            "char_length(payload_sha256) = 64", name="ck_conversation_history_checkpoints_checksum"
        ),
    )
    op.create_table(
        "conversation_history_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("manifest_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_message_id", sa.String(200), nullable=False),
        sa.Column("sequence_start", sa.Integer(), nullable=False),
        sa.Column("sequence_end", sa.Integer(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["manifest_id"], ["conversation_history_manifests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manifest_id",
            "assistant_message_id",
            "sequence_start",
            name="uq_conversation_history_events_range",
        ),
        sa.CheckConstraint(
            "sequence_start >= 0 AND sequence_end >= sequence_start",
            name="ck_conversation_history_events_sequence",
        ),
        sa.CheckConstraint(
            "char_length(payload_sha256) = 64", name="ck_conversation_history_events_checksum"
        ),
    )


def _create_purge_tables() -> None:
    op.create_table(
        "agent_purge_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id_snapshot", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id_snapshot", sa.Uuid(), nullable=False),
        sa.Column("agent_name_snapshot", sa.String(100), nullable=False),
        sa.Column("requester_user_id", sa.Uuid()),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("failure_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "org_id", "agent_id_snapshot", name="uq_agent_purge_receipts_agent"
        ),
        sa.CheckConstraint(
            "state IN ('preparing', 'db_deleted', 'cleanup_pending', 'completed', 'failed')",
            name="ck_agent_purge_receipts_state",
        ),
    )
    op.create_table(
        "agent_purge_cleanup_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("locator_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["receipt_id"], ["agent_purge_receipts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "receipt_id", "kind", "locator", name="uq_agent_purge_cleanup_items_locator"
        ),
        sa.CheckConstraint(
            "kind IN ('agent_image', 'agent_files', 'raw_checkpoint')",
            name="ck_agent_purge_cleanup_items_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="ck_agent_purge_cleanup_items_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_agent_purge_cleanup_items_attempts"),
        sa.CheckConstraint(
            "char_length(locator_sha256) = 64", name="ck_agent_purge_cleanup_items_locator_checksum"
        ),
    )


def upgrade() -> None:
    _add_conversation_lifecycle()
    _detach_retained_agent_references()
    _add_envelope_history()
    _create_run_participants()
    _create_history_tables()
    _create_purge_tables()


def _assert_downgrade_safe() -> None:
    unsafe = op.get_bind().scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM conversations WHERE agent_id IS NULL) + "
            "(SELECT count(*) FROM conversation_runs WHERE agent_id IS NULL) + "
            "(SELECT count(*) FROM conversation_run_inputs WHERE agent_id IS NULL) + "
            "(SELECT count(*) FROM conversation_artifacts WHERE agent_id IS NULL) + "
            "(SELECT count(*) FROM token_usages WHERE agent_id IS NULL) + "
            "(SELECT count(*) FROM mcp_app_invocation_bindings WHERE agent_id IS NULL) + "
            "(SELECT count(*) FROM agent_api_threads WHERE deployment_id IS NULL) + "
            "(SELECT count(*) FROM agent_api_runs WHERE deployment_id IS NULL "
            "OR conversation_run_id IS NOT NULL) + "
            "(SELECT count(*) FROM agent_trigger_runs WHERE trigger_id IS NULL OR agent_id IS NULL "
            "OR conversation_run_id IS NOT NULL) + "
            "(SELECT count(*) FROM conversation_history_manifests) + "
            "(SELECT count(*) FROM agent_purge_receipts) + "
            "(SELECT count(*) FROM conversation_run_agents AS participant "
            "JOIN conversation_runs AS run ON run.id = participant.run_id "
            "WHERE participant.role <> 'root' OR participant.agent_id IS NULL "
            "OR participant.agent_id_snapshot <> participant.agent_id "
            "OR participant.agent_id <> run.agent_id "
            "OR CAST(participant.path AS TEXT) <> '[]')"
        )
    )
    if unsafe:
        msg = "Cannot downgrade retained or purged Agent lifecycle data to m83"
        raise LifecycleDowngradeError(msg)


def downgrade() -> None:
    _assert_downgrade_safe()
    op.drop_table("agent_purge_cleanup_items")
    op.drop_table("agent_purge_receipts")
    op.drop_table("conversation_history_events")
    op.drop_table("conversation_history_checkpoints")
    op.drop_table("conversation_history_manifests")
    op.drop_table("conversation_run_agents")
    with op.batch_alter_table("agent_trigger_runs") as batch:
        batch.drop_constraint("uq_agent_trigger_runs_conversation_run", type_="unique")
        batch.drop_constraint("fk_agent_trigger_runs_conversation_run", type_="foreignkey")
        batch.drop_column("conversation_run_id")
        batch.drop_column("trigger_name_snapshot")
    with op.batch_alter_table("agent_api_runs") as batch:
        batch.drop_constraint("uq_agent_api_runs_conversation_run", type_="unique")
        batch.drop_constraint("fk_agent_api_runs_conversation_run", type_="foreignkey")
        batch.drop_column("conversation_run_id")
    for table in ("agent_api_runs", "agent_api_threads"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("deployment_public_id_snapshot")
            batch.alter_column("deployment_id", existing_type=sa.Uuid(), nullable=False)
        _replace_fk(
            table, "deployment_id", "agent_deployments", f"fk_{table}_deployment_legacy", "CASCADE"
        )
    for table, target in (
        ("agent_trigger_runs", "agent_triggers"),
        ("conversation_runs", "agents"),
        ("conversation_run_inputs", "agents"),
        ("conversation_artifacts", "agents"),
        ("token_usages", "agents"),
        ("mcp_app_invocation_bindings", "agents"),
    ):
        column = "trigger_id" if table == "agent_trigger_runs" else "agent_id"
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.Uuid(), nullable=False)
        _replace_fk(table, column, target, f"fk_{table}_{column}_legacy", "CASCADE")
    with op.batch_alter_table("agent_trigger_runs") as batch:
        batch.alter_column("agent_id", existing_type=sa.Uuid(), nullable=False)
    _replace_fk(
        "agent_trigger_runs", "agent_id", "agents", "fk_agent_trigger_runs_agent_legacy", "CASCADE"
    )
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("ck_conversations_agent_lifecycle_binding", type_="check")
        batch.drop_constraint("ck_conversations_agent_lifecycle_state", type_="check")
        batch.drop_column("agent_purged_at")
        batch.drop_column("agent_archived_at_snapshot")
        batch.drop_column("agent_lifecycle_state")
