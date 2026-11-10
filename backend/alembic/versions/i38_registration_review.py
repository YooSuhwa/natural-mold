"""Registration review of tools and MCP servers (tool-governance-plan 3.7, T5).

``mcp_servers`` and ``tools`` gain ``registration_state`` (``approved`` for every existing
row); a server also keeps the fingerprint of the connection an administrator approved.
``approval_requests`` gains the ``registration`` kind: such a request has no run, it points
at the resource (``resource_type``, ``resource_id``).

Downgrade deletes registration requests, then restores the columns and checks.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i38_registration_review"
down_revision = "i37_resource_policies"
branch_labels = None
depends_on = None

_SOURCES_BEFORE = ("chat", "trigger", "api", "subagent", "workflow")
_SOURCES_AFTER = (*_SOURCES_BEFORE, "registration")
_SHAPE_BEFORE = (
    "(kind = 'tool' AND conversation_id IS NOT NULL AND run_id IS NOT NULL) OR "
    "(kind = 'business' AND workflow_step_run_id IS NOT NULL)"
)
_SHAPE_AFTER = (
    _SHAPE_BEFORE
    + " OR (kind = 'registration' AND resource_type IS NOT NULL AND resource_id IS NOT NULL)"
)
_STATE = "registration_state IN ('approved','pending','rejected')"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ",".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    for table in ("mcp_servers", "tools"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "registration_state", sa.String(20), nullable=False, server_default="approved"
                )
            )
            batch.create_check_constraint(f"ck_{table}_registration_state", _STATE)
    op.add_column(
        "mcp_servers", sa.Column("registration_fingerprint", sa.String(64), nullable=True)
    )
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("resource_type", sa.String(40), nullable=True))
        batch.add_column(sa.Column("resource_id", sa.Uuid(), nullable=True))
        batch.drop_constraint("ck_approval_requests_shape", type_="check")
        batch.drop_constraint("ck_approval_requests_kind", type_="check")
        batch.drop_constraint("ck_approval_requests_source", type_="check")
        batch.create_check_constraint("ck_approval_requests_source", _in("source", _SOURCES_AFTER))
        batch.create_check_constraint(
            "ck_approval_requests_kind", "kind IN ('tool','business','registration')"
        )
        batch.create_check_constraint("ck_approval_requests_shape", _SHAPE_AFTER)
        batch.create_index("ix_approval_requests_resource", ["resource_type", "resource_id"])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM approval_requests WHERE kind = 'registration'"))
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_index("ix_approval_requests_resource")
        batch.drop_constraint("ck_approval_requests_shape", type_="check")
        batch.drop_constraint("ck_approval_requests_kind", type_="check")
        batch.drop_constraint("ck_approval_requests_source", type_="check")
        batch.create_check_constraint("ck_approval_requests_source", _in("source", _SOURCES_BEFORE))
        batch.create_check_constraint("ck_approval_requests_kind", "kind IN ('tool','business')")
        batch.create_check_constraint("ck_approval_requests_shape", _SHAPE_BEFORE)
        batch.drop_column("resource_id")
        batch.drop_column("resource_type")
    op.drop_column("mcp_servers", "registration_fingerprint")
    for table in ("mcp_servers", "tools"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"ck_{table}_registration_state", type_="check")
            batch.drop_column("registration_state")
