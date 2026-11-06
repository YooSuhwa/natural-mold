"""Agent API deployment approval policy (feature plan v2 순번 21, 17.3).

``agent_deployments.approval_policy``: what an Agent API run does with a tool call that
needs a person's approval. ``blocked`` (the default, and what every deployment did so far)
answers a BLOCKED result for that call; ``inbox`` pauses the run, sends the call to the
deployment owner's approval inbox and answers the caller ``waiting``; the caller reads the
outcome from ``GET /v1/runs/{id}``.

``approval_requests.api_run_id``: the Agent API run that waits on a request, as
``trigger_run_id`` is for a schedule run. Downgrade drops both columns.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i20_deployment_approval_policy"
down_revision = "i19_emergency_stops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_deployments") as batch:
        batch.add_column(
            sa.Column("approval_policy", sa.String(20), nullable=False, server_default="blocked")
        )
        batch.create_check_constraint(
            "ck_agent_deployments_approval_policy", "approval_policy IN ('inbox', 'blocked')"
        )
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("api_run_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_approval_requests_api_run_id_agent_api_runs",
            "agent_api_runs",
            ["api_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("fk_approval_requests_api_run_id_agent_api_runs", type_="foreignkey")
        batch.drop_column("api_run_id")
    with op.batch_alter_table("agent_deployments") as batch:
        batch.drop_constraint("ck_agent_deployments_approval_policy", type_="check")
        batch.drop_column("approval_policy")
