"""M54: agent identity.

Revision ID: m54_agent_identity
Revises: m53_performance_hot_path_indexes
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m54_agent_identity"
down_revision = "m53_performance_hot_path_indexes"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column("agents", sa.Column("runtime_name", sa.String(length=40), nullable=True))
    op.add_column(
        "agents",
        sa.Column(
            "identity_mode",
            sa.String(length=20),
            nullable=False,
            server_default="fixed",
        ),
    )

    if _is_postgres():
        op.execute(
            """
            UPDATE agents
            SET runtime_name = 'agent_' || left(replace(id::text, '-', ''), 8)
            WHERE runtime_name IS NULL
            """
        )
    else:
        op.execute(
            """
            UPDATE agents
            SET runtime_name = 'agent_' || substr(replace(id, '-', ''), 1, 8)
            WHERE runtime_name IS NULL
            """
        )
    if _is_postgres():
        op.alter_column("agents", "runtime_name", nullable=False)
        op.create_unique_constraint("uq_agents_runtime_name", "agents", ["runtime_name"])
        op.create_check_constraint(
            "ck_agents_identity_mode",
            "agents",
            "identity_mode in ('fixed', 'per_user')",
        )
    else:
        with op.batch_alter_table("agents") as batch_op:
            batch_op.alter_column(
                "runtime_name",
                existing_type=sa.String(length=40),
                nullable=False,
            )
            batch_op.create_unique_constraint("uq_agents_runtime_name", ["runtime_name"])
            batch_op.create_check_constraint(
                "ck_agents_identity_mode",
                "identity_mode in ('fixed', 'per_user')",
            )

    op.add_column(
        "agent_trigger_runs",
        sa.Column("identity_mode", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "agent_trigger_runs",
        sa.Column("agent_runtime_name", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "agent_trigger_runs",
        sa.Column("credential_subject_user_id", sa.Uuid(), nullable=True),
    )
    if _is_postgres():
        op.create_foreign_key(
            "fk_agent_trigger_runs_credential_subject_user_id",
            "agent_trigger_runs",
            "users",
            ["credential_subject_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    else:
        with op.batch_alter_table("agent_trigger_runs") as batch_op:
            batch_op.create_foreign_key(
                "fk_agent_trigger_runs_credential_subject_user_id",
                "users",
                ["credential_subject_user_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    if _is_postgres():
        op.drop_constraint(
            "fk_agent_trigger_runs_credential_subject_user_id",
            "agent_trigger_runs",
            type_="foreignkey",
        )
        op.drop_column("agent_trigger_runs", "credential_subject_user_id")
        op.drop_column("agent_trigger_runs", "agent_runtime_name")
        op.drop_column("agent_trigger_runs", "identity_mode")
        op.drop_constraint("ck_agents_identity_mode", "agents", type_="check")
        op.drop_constraint("uq_agents_runtime_name", "agents", type_="unique")
        op.drop_column("agents", "identity_mode")
        op.drop_column("agents", "runtime_name")
    else:
        with op.batch_alter_table("agent_trigger_runs") as batch_op:
            batch_op.drop_column("credential_subject_user_id")
            batch_op.drop_column("agent_runtime_name")
            batch_op.drop_column("identity_mode")
        with op.batch_alter_table("agents") as batch_op:
            batch_op.drop_constraint("ck_agents_identity_mode", type_="check")
            batch_op.drop_constraint("uq_agents_runtime_name", type_="unique")
            batch_op.drop_column("identity_mode")
            batch_op.drop_column("runtime_name")
