"""Workflow loop rounds (workflow-loop-plan 3.3).

``workflow_step_runs`` gains ``round`` (1 for the first run of a node in a run, +1 each time a
loop runs it again). The one-row-per-node unique key becomes one row per node and round, so
earlier rounds are kept, never overwritten. Every existing row is round 1, so the new key
holds at once.

Downgrade deletes the rows of later rounds (runs that looped), then restores the old key.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i30_workflow_step_rounds"
down_revision = "i29_workflow_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_step_runs") as batch:
        batch.add_column(sa.Column("round", sa.Integer(), nullable=False, server_default="1"))
        batch.drop_constraint("uq_workflow_step_runs_node", type_="unique")
        batch.create_unique_constraint(
            "uq_workflow_step_runs_node_round", ["run_id", "node_id", "round"]
        )
        batch.create_check_constraint("ck_workflow_step_runs_round", "round >= 1")


def downgrade() -> None:
    op.execute("DELETE FROM workflow_step_runs WHERE round > 1")
    with op.batch_alter_table("workflow_step_runs") as batch:
        batch.drop_constraint("ck_workflow_step_runs_round", type_="check")
        batch.drop_constraint("uq_workflow_step_runs_node_round", type_="unique")
        batch.create_unique_constraint("uq_workflow_step_runs_node", ["run_id", "node_id"])
        batch.drop_column("round")
