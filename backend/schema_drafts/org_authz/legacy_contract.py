"""Portable column contracts for existing tables, isolated from application models."""

import sqlalchemy as sa

from schema_drafts.org_authz.scope_columns import M81_SCOPED, M89_SCOPED, TENANT_ONLY


def pair(metadata: sa.MetaData, name: str) -> None:
    table = metadata.tables[name]
    table.append_column(sa.Column("org_id", sa.Uuid(), nullable=True))
    if "tenant_id" not in table.c:
        table.append_column(
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", name=f"fk_{name}_tenant", ondelete="RESTRICT"),
            )
        )
    table.append_constraint(
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{name}_org_tenant",
            ondelete="RESTRICT",
        )
    )


def apply_m81(metadata: sa.MetaData) -> None:
    """Match the nullable migration stage before backfill and NOT NULL enforcement."""
    for name in M81_SCOPED:
        pair(metadata, name)
    for name in TENANT_ONLY:
        metadata.tables[name].append_column(
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", name=f"fk_{name}_tenant", ondelete="RESTRICT"),
            )
        )
    metadata.tables["users"].append_column(
        sa.Column(
            "last_active_org_id",
            sa.Uuid(),
            sa.ForeignKey(
                "organizations.id",
                name="fk_users_last_active_org",
                ondelete="SET NULL",
                use_alter=True,
            ),
        )
    )
    conversations = metadata.tables["conversations"]
    conversations.append_column(
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", name="fk_conversations_owner", ondelete="CASCADE"),
        )
    )
    agents = metadata.tables["agents"]
    agents.append_column(sa.Column("archived_at", sa.DateTime(timezone=True)))
    agents.append_column(
        sa.Column(
            "archived_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", name="fk_agents_archived_by", ondelete="SET NULL"),
        )
    )
    agents.append_column(sa.Column("monthly_spend_limit", sa.Numeric(20, 8)))
    agents.append_constraint(
        sa.CheckConstraint(
            "monthly_spend_limit IS NULL OR monthly_spend_limit >= 0", name="monthly_spend_limit"
        )
    )
    credentials = metadata.tables["credentials"]
    credentials.append_column(
        sa.Column("scope", sa.String(20), nullable=False, server_default="personal")
    )
    credentials.append_constraint(
        sa.CheckConstraint("scope IN ('personal','tenant','platform')", name="scope")
    )
    items = metadata.tables["marketplace_items"]
    items.append_column(
        sa.Column("tenant_publication", sa.String(20), nullable=False, server_default="none")
    )
    items.append_constraint(
        sa.CheckConstraint(
            "tenant_publication IN ('none','pending','approved','rejected')",
            name="tenant_publication",
        )
    )
    for action in ("requested", "reviewed"):
        items.append_column(
            sa.Column(
                f"tenant_publication_{action}_by",
                sa.Uuid(),
                sa.ForeignKey(
                    "users.id",
                    name=f"fk_marketplace_items_publication_{action}_by",
                    ondelete="SET NULL",
                ),
            )
        )
        items.append_column(
            sa.Column(f"tenant_publication_{action}_at", sa.DateTime(timezone=True))
        )
    items.append_column(sa.Column("tenant_publication_note", sa.Text()))


def apply_later_columns(metadata: sa.MetaData) -> None:
    """The later contract is used for schema review, never for m82 row selection."""
    for name in M89_SCOPED:
        pair(metadata, name)
    for name in ("agent_tools", "agent_mcp_tools", "agent_skills", "agent_subagents"):
        table = metadata.tables[name]
        table.append_column(
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        table.append_column(
            sa.Column("delegated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
        )
        table.append_column(
            sa.Column("link_status", sa.String(20), nullable=False, server_default="active")
        )
        table.append_column(sa.Column("broken_reason", sa.Text()))
        table.append_constraint(
            sa.CheckConstraint("link_status IN ('active','broken')", name="link_status")
        )
    metadata.tables["agents"].append_column(
        sa.Column(
            "llm_credential_delegated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")
        )
    )
    for name in ("tools", "mcp_servers"):
        table = metadata.tables[name]
        table.append_column(
            sa.Column("credential_mode", sa.String(20), nullable=False, server_default="shared")
        )
        table.append_column(sa.Column("credential_definition_key", sa.String(80)))
        table.append_constraint(
            sa.CheckConstraint(
                "credential_mode IN ('none','shared','per_user')", name="credential_mode"
            )
        )
    metadata.tables["credentials"].append_column(
        sa.Column("allowed_hosts", sa.JSON(), nullable=False, server_default="[]")
    )
    metadata.tables["agent_trigger_runs"].append_column(sa.Column("reason_code", sa.String(40)))
    metadata.tables["token_usages"].append_column(
        sa.Column("caller_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))
    )
