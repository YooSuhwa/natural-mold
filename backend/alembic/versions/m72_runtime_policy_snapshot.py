"""Snapshot runtime policy on conversations and conversation runs."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m72_runtime_policy_snapshot"
down_revision = "m71_runtime_policy"
branch_labels = None
depends_on = None

_DEFAULT_POLICY = {
    "version": 1,
    "filesystem": {"mode": "artifact_write"},
    "todo": {"enabled": True},
    "summarization": {"mode": "auto"},
}
_DEFAULT_HASH = "6b4a6a3dd09f19146168b613f9f903526a91a374e71cfd28166d458a79841340"


def upgrade() -> None:
    op.add_column("conversations", sa.Column("runtime_policy_snapshot", sa.JSON(), nullable=True))
    op.add_column("conversations", sa.Column("runtime_policy_version", sa.Integer(), nullable=True))
    op.add_column("conversations", sa.Column("runtime_policy_hash", sa.String(64), nullable=True))
    op.add_column("conversations", sa.Column("runtime_policy_source", sa.String(20), nullable=True))
    op.add_column(
        "conversation_runs", sa.Column("runtime_policy_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "conversation_runs", sa.Column("runtime_policy_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "conversation_runs", sa.Column("runtime_policy_source", sa.String(20), nullable=True)
    )

    bind = op.get_bind()
    agents = sa.table(
        "agents",
        sa.column("id"),
        sa.column("runtime_profile"),
    )
    conversations = sa.table(
        "conversations",
        sa.column("id"),
        sa.column("agent_id"),
        sa.column("runtime_policy_snapshot", sa.JSON()),
        sa.column("runtime_policy_version"),
        sa.column("runtime_policy_hash"),
        sa.column("runtime_policy_source"),
    )
    runs = sa.table(
        "conversation_runs",
        sa.column("conversation_id"),
        sa.column("runtime_policy_version"),
        sa.column("runtime_policy_hash"),
        sa.column("runtime_policy_source"),
    )
    profile = (
        sa.select(agents.c.runtime_profile)
        .where(agents.c.id == conversations.c.agent_id)
        .scalar_subquery()
    )
    bind.execute(
        sa.update(conversations).values(
            runtime_policy_snapshot=_DEFAULT_POLICY,
            runtime_policy_version=1,
            runtime_policy_hash=_DEFAULT_HASH,
            runtime_policy_source=sa.case(
                (profile == "skill_builder", "server_owned"),
                else_="legacy_compat",
            ),
        )
    )
    conversation_version = (
        sa.select(conversations.c.runtime_policy_version)
        .where(conversations.c.id == runs.c.conversation_id)
        .scalar_subquery()
    )
    conversation_hash = (
        sa.select(conversations.c.runtime_policy_hash)
        .where(conversations.c.id == runs.c.conversation_id)
        .scalar_subquery()
    )
    conversation_source = (
        sa.select(conversations.c.runtime_policy_source)
        .where(conversations.c.id == runs.c.conversation_id)
        .scalar_subquery()
    )
    bind.execute(
        sa.update(runs).values(
            runtime_policy_version=conversation_version,
            runtime_policy_hash=conversation_hash,
            runtime_policy_source=conversation_source,
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("conversation_runs") as batch_op:
        batch_op.drop_column("runtime_policy_source")
        batch_op.drop_column("runtime_policy_hash")
        batch_op.drop_column("runtime_policy_version")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("runtime_policy_source")
        batch_op.drop_column("runtime_policy_hash")
        batch_op.drop_column("runtime_policy_version")
        batch_op.drop_column("runtime_policy_snapshot")
