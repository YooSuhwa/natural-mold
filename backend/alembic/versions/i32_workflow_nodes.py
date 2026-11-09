"""The remaining workflow nodes (workflow-nodes-plan 3.1-3.2).

``workflow_step_runs`` gains child rows: a ``for_each`` or ``parallel`` step keeps one row
per agent it runs (``parent_step_id`` + ``branch_key``; the child's ``node_id`` is
``<node>.<key>``, hence the wider column). ``branch`` widens for switch case names.
``workflow_runs.files`` keeps the uploads given to ``file`` inputs (the input itself holds
the file name templates see). The notify step's notification kind is i33.

Downgrade removes the child rows and the columns.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i32_workflow_nodes"
down_revision = "i31_workflow_chat_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_step_runs") as batch:
        batch.add_column(sa.Column("parent_step_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("branch_key", sa.String(64), nullable=True))
        batch.alter_column("node_id", existing_type=sa.String(64), type_=sa.String(130))
        batch.alter_column("branch", existing_type=sa.String(20), type_=sa.String(64))
        batch.create_foreign_key(
            "fk_workflow_step_runs_parent_step_id_workflow_step_runs",
            "workflow_step_runs",
            ["parent_step_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_workflow_step_runs_parent_step_id", ["parent_step_id"])
    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(sa.Column("files", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_column("files")
    op.execute(sa.text("DELETE FROM workflow_step_runs WHERE parent_step_id IS NOT NULL"))
    with op.batch_alter_table("workflow_step_runs") as batch:
        batch.drop_index("ix_workflow_step_runs_parent_step_id")
        batch.drop_constraint(
            "fk_workflow_step_runs_parent_step_id_workflow_step_runs", type_="foreignkey"
        )
        batch.alter_column("branch", existing_type=sa.String(64), type_=sa.String(20))
        batch.alter_column("node_id", existing_type=sa.String(130), type_=sa.String(64))
        batch.drop_column("branch_key")
        batch.drop_column("parent_step_id")
