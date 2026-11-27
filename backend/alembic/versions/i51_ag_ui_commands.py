"""Bind public AG-UI run IDs to durable chat executions or queued inputs."""

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_activation import app_role
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i51_ag_ui_commands"
down_revision = "i50_chat_model_attempts"
branch_labels = None
depends_on = None

_TABLE = "ag_ui_command_receipts"


def _install_queue_claim_guard(schema: str) -> None:
    # An older worker can claim this queue, but cannot resolve a selected
    # branch. Roll back its whole transaction, including the physical run.
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION moldy_guard_ag_ui_queue_claim() RETURNS trigger
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
            CREATE TRIGGER ck_ag_ui_selected_queue_claim
            BEFORE UPDATE OF status, run_id, checkpoint_id ON conversation_run_inputs
            FOR EACH ROW WHEN (OLD.status = 'pending' AND OLD.source = 'chat'
                AND NEW.status = 'claimed' AND NEW.checkpoint_id IS NULL)
            EXECUTE FUNCTION moldy_guard_ag_ui_queue_claim()
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("public_run_id", sa.String(200), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("projection_version", sa.String(16), server_default="1", nullable=False),
        sa.Column("checkpoint_id", sa.String(64)),
        sa.Column("parent_public_run_id", sa.String(200)),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey(
                "conversation_runs.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"
            ),
        ),
        sa.Column(
            "input_id",
            sa.Uuid(),
            sa.ForeignKey(
                "conversation_run_inputs.id",
                ondelete="NO ACTION",
                deferrable=True,
                initially="DEFERRED",
            ),
        ),
        sa.Column("delivery_encrypted", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_ag_ui_command_receipts_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name="fk_ag_ui_command_receipts_org_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("run_id", name="uq_ag_ui_commands_run"),
        sa.UniqueConstraint("input_id", name="uq_ag_ui_commands_input"),
        sa.CheckConstraint(
            "(run_id IS NULL) <> (input_id IS NULL)", name="ck_ag_ui_commands_one_target"
        ),
        sa.CheckConstraint(
            "delivery_encrypted IS NULL OR run_id IS NOT NULL", name="ck_ag_ui_commands_delivery"
        ),
        sa.CheckConstraint(
            "length(public_run_id) BETWEEN 1 AND 200", name="ck_ag_ui_commands_public_id"
        ),
        sa.CheckConstraint(
            "length(parent_public_run_id) BETWEEN 1 AND 200", name="ck_ag_ui_commands_parent_id"
        ),
        sa.CheckConstraint("length(request_digest) = 64", name="ck_ag_ui_commands_digest"),
        sa.CheckConstraint(
            "length(projection_version) BETWEEN 1 AND 16", name="ck_ag_ui_commands_projection"
        ),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        quote = bind.dialect.identifier_preparer.quote
        for statement in create_policy_sql(quote, table_policy(quote, _TABLE)):
            op.execute(sa.text(statement))
        if settings.db_rls_enabled:
            for statement in enable_sql(quote, [_TABLE]):
                op.execute(sa.text(statement))
        role = app_role(bind)
        if role is not None:
            op.execute(
                sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {quote(_TABLE)} TO {quote(role)}")
            )
        schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()
        _install_queue_claim_guard(bind.dialect.identifier_preparer.quote_identifier(schema))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP TRIGGER ck_ag_ui_selected_queue_claim ON conversation_run_inputs"))
        op.execute(sa.text("DROP FUNCTION moldy_guard_ag_ui_queue_claim()"))
    op.drop_table(_TABLE)
