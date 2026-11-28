"""Require a checkpoint pin when claiming a selected AG-UI conversation branch."""

import sqlalchemy as sa

from alembic import op

revision = "i52_ag_ui_queue_claim_guard"
down_revision = "i51_ag_ui_commands"
branch_labels = None
depends_on = None


def _install_queue_claim_guard(schema: str) -> None:
    # An older worker can claim this queue, but cannot resolve a selected
    # branch. Roll back its whole transaction, including the physical run.
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION moldy_guard_ag_ui_queue_claim() RETURNS trigger
            LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, {schema}, pg_temp
            AS $guard$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM ag_ui_command_receipts AS r
                    JOIN conversations AS c ON c.id = r.conversation_id
                        AND c.user_id = r.user_id
                        AND c.org_id = r.org_id
                        AND c.tenant_id = r.tenant_id
                    WHERE r.input_id = OLD.id AND r.conversation_id = OLD.conversation_id
                        AND r.user_id = OLD.user_id AND c.agent_id = OLD.agent_id
                        AND c.active_branch_checkpoint_id IS NOT NULL
                ) THEN
                    RAISE EXCEPTION USING ERRCODE = '23514',
                        CONSTRAINT = 'ck_ag_ui_selected_queue_claim',
                        MESSAGE = 'A selected conversation branch requires a pinned queue claim';
                END IF;
                RETURN NEW;
            END;
            $guard$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE TRIGGER ck_ag_ui_selected_queue_claim
            BEFORE UPDATE OF status, run_id, checkpoint_id ON conversation_run_inputs
            FOR EACH ROW WHEN (OLD.status = 'pending' AND OLD.source = 'chat'
                AND NEW.status = 'claimed' AND NEW.checkpoint_id IS NULL)
            EXECUTE FUNCTION moldy_guard_ag_ui_queue_claim()
            """
        )
    )


def _fail_unstarted_unpinned_claims() -> None:
    # An earlier worker could commit a real run before recording the selected fork.
    # Lock the same physical run its worker must own before graph execution. If it
    # already started, PostgreSQL rechecks the predicate and leaves the run alone.
    op.execute(
        sa.text(
            """
            WITH ambiguous AS MATERIALIZED (
                SELECT q.id AS input_id, run.id AS run_id
                FROM ag_ui_command_receipts AS r
                JOIN conversations AS c ON c.id = r.conversation_id AND c.user_id = r.user_id
                    AND c.org_id = r.org_id AND c.tenant_id = r.tenant_id
                JOIN conversation_run_inputs AS q ON q.id = r.input_id
                    AND q.conversation_id = c.id AND q.user_id = c.user_id
                    AND q.agent_id = c.agent_id
                JOIN conversation_runs AS run ON run.id = q.run_id
                    AND run.conversation_id = c.id AND run.user_id = c.user_id
                    AND run.agent_id = c.agent_id AND run.org_id = c.org_id
                    AND run.tenant_id = c.tenant_id
                WHERE r.checkpoint_id IS NULL AND c.active_branch_checkpoint_id IS NOT NULL
                    AND q.source = 'chat' AND q.status = 'claimed' AND q.checkpoint_id IS NULL
                    AND run.source = 'chat' AND (run.run_source IS NULL OR run.run_source = 'CHAT')
                    AND run.parent_run_id IS NULL AND run.is_active AND run.status = 'queued'
                    AND run.metadata_json ->> 'queue_input_id' = q.id::text
                    AND run.metadata_json ->> 'client_request_id' = q.client_request_id
                    AND run.metadata_json ->> 'checkpoint_id' IS NULL
                    AND (run.metadata_json -> 'ag_ui_default_branch_at_claim')::text
                        IS DISTINCT FROM 'true'
                    AND run.worker_instance_id IS NULL AND run.owner_token IS NULL
                    AND run.owner_generation = 0 AND run.recovery_attempts = 0
                    AND run.started_at IS NULL AND run.heartbeat_at IS NULL
                    AND run.completed_at IS NULL AND run.cancel_requested_at IS NULL
                    AND run.last_event_id IS NULL AND run.runtime_materialized_at IS NULL
                    AND run.runtime_materialization_sha256 IS NULL
                    AND run.recovery_runtime_digest IS NULL AND run.recovery_model_attempts IS NULL
                    AND run.metadata_json ->> 'partial_model_output' IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM conversation_run_tool_receipts WHERE run_id = run.id
                    )
                FOR UPDATE OF c, run, q
            ), failed AS (
                UPDATE conversation_runs AS run
                SET status = 'failed', is_active = false, error_code = 'queue_branch_unpinned',
                    error_message = 'The queued run has no recorded branch checkpoint.',
                    completed_at = timezone('UTC', statement_timestamp()),
                    updated_at = timezone('UTC', statement_timestamp())
                FROM ambiguous WHERE run.id = ambiguous.run_id
                RETURNING run.id
            )
            UPDATE conversation_run_inputs AS q
            SET status = 'failed', revision = q.revision + 1,
                updated_at = timezone('UTC', statement_timestamp())
            FROM ambiguous JOIN failed ON failed.id = ambiguous.run_id
            WHERE q.id = ambiguous.input_id
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Adopt any earlier installation in place without removing the guard or its receipts.
        schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()
        _install_queue_claim_guard(bind.dialect.identifier_preparer.quote_identifier(schema))
        bind.execute(sa.text("SET LOCAL row_security = off"))
        _fail_unstarted_unpinned_claims()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Serialize the check with branch changes, receipt creation, and claim/recovery.
    # Refuse to examine a filtered subset if the migration role lacks RLS authority.
    bind.execute(sa.text("SET LOCAL row_security = off"))
    bind.execute(
        sa.text(
            "LOCK TABLE conversations, conversation_run_inputs, ag_ui_command_receipts, "
            "conversation_runs IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if bind.scalar(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM ag_ui_command_receipts AS r
                JOIN conversations AS c ON c.id = r.conversation_id AND c.user_id = r.user_id
                    AND c.org_id = r.org_id AND c.tenant_id = r.tenant_id
                JOIN conversation_run_inputs AS q ON q.id = r.input_id
                    AND q.conversation_id = c.id AND q.user_id = c.user_id
                    AND q.agent_id = c.agent_id
                WHERE q.source = 'chat' AND q.checkpoint_id IS NULL
                    AND c.active_branch_checkpoint_id IS NOT NULL
                    AND (q.status = 'pending' OR (q.status = 'claimed' AND EXISTS (
                        SELECT 1 FROM conversation_runs AS run
                        WHERE run.id = q.run_id AND run.conversation_id = c.id
                            AND run.user_id = c.user_id AND run.agent_id = c.agent_id
                            AND run.org_id = c.org_id AND run.tenant_id = c.tenant_id
                            AND run.status = 'queued' AND run.worker_instance_id IS NULL
                            AND (run.metadata_json -> 'ag_ui_default_branch_at_claim')::text
                                IS DISTINCT FROM 'true'
                    )))
            )
            """
        )
    ):
        raise RuntimeError(
            "Cannot downgrade while selected AG-UI inputs need a queue claim guard. "
            "Finish or cancel these inputs before removing the guard."
        )

    op.execute(sa.text("DROP TRIGGER ck_ag_ui_selected_queue_claim ON conversation_run_inputs"))
    op.execute(sa.text("DROP FUNCTION moldy_guard_ag_ui_queue_claim()"))
