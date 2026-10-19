"""Frozen m77 agent_api_key_deployments fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_api_key_deployments",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("api_key_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["agent_deployments.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["agent_api_keys.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("api_key_id", "deployment_id", name="uq_agent_api_key_deployment"),
    )
    sa.Index(
        "ix_agent_api_key_deployments_deployment_id",
        metadata.tables["agent_api_key_deployments"].c["deployment_id"],
        unique=False,
    )
