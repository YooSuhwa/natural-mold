"""Tools allowed for one conversation (approval-modes-plan 3.3, Q5).

``conversation_tool_grants``: a tool lifted for one conversation, at most one live row per
tool (partial unique index), revoked by stamping ``revoked_at``. RLS child of
``conversations``.

Downgrade drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.migrations.org_authz_v1.rls_policies import create_policy_sql, enable_sql, table_policy

revision = "i35_conversation_tool_grants"
down_revision = "i34_workflow_starts"
branch_labels = None
depends_on = None

_TABLE = "conversation_tool_grants"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(40), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=f"fk_{_TABLE}_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["users.id"], name=f"fk_{_TABLE}_granted_by_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
    )
    op.create_index(f"ix_{_TABLE}_conversation_id", _TABLE, ["conversation_id"])
    op.create_index(
        "uq_conversation_tool_grants_live",
        _TABLE,
        ["conversation_id", "tool_name"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    quote = bind.dialect.identifier_preparer.quote
    for statement in create_policy_sql(quote, table_policy(quote, _TABLE)):
        op.execute(sa.text(statement))
    if settings.db_rls_enabled:
        for statement in enable_sql(quote, [_TABLE]):
            op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_index("uq_conversation_tool_grants_live", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_conversation_id", table_name=_TABLE)
    op.drop_table(_TABLE)
