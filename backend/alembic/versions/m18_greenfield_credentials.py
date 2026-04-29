"""M18: greenfield rewrite — credentials, tools, mcp, skills, models.

Revision ID: m18_greenfield_credentials
Revises: m17_add_agent_subagents
Create Date: 2026-04-29

Drops the legacy credential / tools / connections / skills / models / mcp
schema accumulated through m6~m17 and recreates the greenfield tables defined
by ``app.models.{credential,credential_audit_log,credential_default,tool,
mcp_server,mcp_tool,skill,model}`` plus an ``agents.llm_credential_id`` FK.

This migration is **non-reversible** — PoC-stage data loss is accepted.
Restore from backup if you need to roll back.

Note: an unrelated ``m13_drop_mcp_legacy.py`` migration exists in the chain;
the spec name "m13_greenfield_credentials" was repurposed to ``m18`` to
avoid revision-id collision with the historical m13.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m18_greenfield_credentials"
down_revision = "m17_add_agent_subagents"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers — dialect-aware UUID column type and partial unique index.
# ---------------------------------------------------------------------------


def _uuid_col() -> sa.types.TypeEngine:
    """SQLAlchemy UUID column type that works on both PostgreSQL and SQLite."""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects import postgresql

        return postgresql.UUID(as_uuid=True)
    return sa.String(36)


def _utc_now_default() -> sa.sql.elements.TextClause:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


def _drop_table_if_exists(name: str) -> None:
    """Drop a table only if it exists (handles fresh DBs that never had it)."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if name in inspector.get_table_names():
        op.drop_table(name)


def _drop_constraint_if_exists(name: str, table: str, type_: str = "foreignkey") -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    if type_ == "foreignkey":
        existing = {fk["name"] for fk in inspector.get_foreign_keys(table)}
    elif type_ == "unique":
        existing = {uq["name"] for uq in inspector.get_unique_constraints(table)}
    else:
        existing = set()
    if name in existing:
        op.drop_constraint(name, table, type_=type_)


def _drop_column_if_exists(table: str, column: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns(table)}
    if column in cols:
        op.drop_column(table, column)


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    uuid_type = _uuid_col()
    now_default = _utc_now_default()

    # 1) Detach the FK we are about to recreate so dropping ``credentials``
    #    succeeds even on PostgreSQL (the SET NULL FK survives a DROP TABLE
    #    of the parent only when it was created with CASCADE policies).
    _drop_constraint_if_exists("fk_tools_credential_id", "tools")
    _drop_constraint_if_exists("fk_connections_credential_id", "connections")

    # 2) Drop tables in child→parent order. Existing m17 chain leaves these
    #    populated; the greenfield m18 wipes them all.
    for table in (
        "agent_tools",
        "agent_skills",
        "mcp_tools",
        "mcp_servers",
        "tools",
        "skills",
        "credential_defaults",
        "credential_audit_logs",
        "credentials",
        "connections",
        "models",
        "llm_providers",
    ):
        _drop_table_if_exists(table)

    # Drop the ``api_key_encrypted`` column off ``models`` and ``llm_providers``
    # historically lived elsewhere — handled by full table drop above. The
    # ``agents`` table needs the new FK column; remove any stale one first.
    _drop_column_if_exists("agents", "llm_credential_id")

    # 3) Rebuild the greenfield schema.

    op.create_table(
        "credentials",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("definition_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("data_encrypted", sa.Text(), nullable=False),
        sa.Column("key_id", sa.String(16), nullable=False),
        sa.Column("field_keys", sa.JSON(), nullable=True),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now_default),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=now_default),
    )
    op.create_index(
        "ix_credentials_user_definition",
        "credentials",
        ["user_id", "definition_key"],
    )

    op.create_table(
        "credential_audit_logs",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "credential_id",
            uuid_type,
            sa.ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="api"),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now_default),
    )
    op.create_index(
        "ix_credential_audit_logs_credential_created",
        "credential_audit_logs",
        ["credential_id", "created_at"],
    )

    op.create_table(
        "credential_defaults",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_kind", sa.String(40), nullable=False),
        sa.Column("scope_key", sa.String(120), nullable=False),
        sa.Column(
            "credential_id",
            uuid_type,
            sa.ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now_default),
    )
    op.create_index(
        "uq_credential_defaults_user_scope",
        "credential_defaults",
        ["user_id", "scope_kind", "scope_key"],
        unique=True,
    )

    op.create_table(
        "models",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cost_per_input_token", sa.Numeric(12, 8), nullable=True),
        sa.Column("cost_per_output_token", sa.Numeric(12, 8), nullable=True),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("input_modalities", sa.JSON(), nullable=True),
        sa.Column("output_modalities", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now_default),
    )

    op.create_table(
        "tools",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("definition_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column(
            "credential_id",
            uuid_type,
            sa.ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now_default),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=now_default),
    )
    op.create_index("ix_tools_user_definition", "tools", ["user_id", "definition_key"])

    op.create_table(
        "mcp_servers",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("transport", sa.String(20), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("command", sa.String(500), nullable=True),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("env_vars", sa.JSON(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=True),
        sa.Column(
            "credential_id",
            uuid_type,
            sa.ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("last_pinged_at", sa.DateTime(), nullable=True),
        sa.Column("last_tool_count", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now_default),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=now_default),
    )

    op.create_table(
        "mcp_tools",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "server_id",
            uuid_type,
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now_default),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=now_default),
        sa.UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),
    )

    op.create_table(
        "skills",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("slug", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="text"),
        sa.Column("storage_path", sa.String(500), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.String(40), nullable=True),
        sa.Column("package_metadata", sa.JSON(), nullable=True),
        sa.Column("used_by_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_modified_at", sa.DateTime(), nullable=False, server_default=now_default),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now_default),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=now_default),
        sa.UniqueConstraint("user_id", "slug", name="uq_skills_user_slug"),
    )

    op.create_table(
        "agent_tools",
        sa.Column(
            "agent_id",
            uuid_type,
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tool_id",
            uuid_type,
            sa.ForeignKey("tools.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "agent_skills",
        sa.Column(
            "agent_id",
            uuid_type,
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "skill_id",
            uuid_type,
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # 4) Add llm_credential_id FK to agents.
    op.add_column(
        "agents",
        sa.Column("llm_credential_id", uuid_type, nullable=True),
    )
    op.create_foreign_key(
        "fk_agents_llm_credential_id",
        "agents",
        "credentials",
        ["llm_credential_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "m18 is intentionally non-reversible — restore from backup"
    )
