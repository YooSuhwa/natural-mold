"""Workflow business approvals in the approval inbox (순번 23, workflow-plan 3.4, W2b).

``approval_requests`` gains ``kind``: ``tool`` (every existing row: a run paused before a
tool call) or ``business`` (a workflow approval node). A business request has no
conversation run, so ``conversation_id`` and ``run_id`` become nullable and the request
points at the waiting step (``workflow_run_id``, ``workflow_step_run_id``, both cascading).
``source`` allows ``workflow``. A shape check keeps tool rows complete.

Downgrade deletes business requests, then restores the columns and checks.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i29_workflow_approvals"
down_revision = "i28_workflow_runs"
branch_labels = None
depends_on = None

_SOURCES_BEFORE = ("chat", "trigger", "api", "subagent")
_SOURCES_AFTER = (*_SOURCES_BEFORE, "workflow")
_SHAPE = (
    "(kind = 'tool' AND conversation_id IS NOT NULL AND run_id IS NOT NULL) OR "
    "(kind = 'business' AND workflow_step_run_id IS NOT NULL)"
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ",".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("kind", sa.String(20), nullable=False, server_default="tool"))
        batch.add_column(sa.Column("workflow_run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("workflow_step_run_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_approval_requests_workflow_run_id_workflow_runs",
            "workflow_runs",
            ["workflow_run_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_approval_requests_workflow_step_run_id_workflow_step_runs",
            "workflow_step_runs",
            ["workflow_step_run_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.alter_column("conversation_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("run_id", existing_type=sa.Uuid(), nullable=True)
        batch.drop_constraint("ck_approval_requests_source", type_="check")
        batch.create_check_constraint("ck_approval_requests_source", _in("source", _SOURCES_AFTER))
        batch.create_check_constraint("ck_approval_requests_kind", "kind IN ('tool','business')")
        batch.create_check_constraint("ck_approval_requests_shape", _SHAPE)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM approval_requests WHERE kind = 'business'"))
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("ck_approval_requests_shape", type_="check")
        batch.drop_constraint("ck_approval_requests_kind", type_="check")
        batch.drop_constraint("ck_approval_requests_source", type_="check")
        batch.create_check_constraint("ck_approval_requests_source", _in("source", _SOURCES_BEFORE))
        batch.alter_column("run_id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column("conversation_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_constraint(
            "fk_approval_requests_workflow_step_run_id_workflow_step_runs", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_approval_requests_workflow_run_id_workflow_runs", type_="foreignkey"
        )
        batch.drop_column("workflow_step_run_id")
        batch.drop_column("workflow_run_id")
        batch.drop_column("kind")
