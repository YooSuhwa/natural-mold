"""Explicit m81/m89 column contracts; all tenant/org FK pairs are named."""

import sqlalchemy as sa

from alembic import op

M81_SCOPED = (
    "agents",
    "skills",
    "mcp_servers",
    "credentials",
    "tools",
    "marketplace_items",
    "conversations",
    "conversation_artifacts",
    "message_attachments",
    "memory_records",
    "memory_proposals",
    "builder_sessions",
    "skill_builder_sessions",
    "agent_blueprints",
    "marketplace_installations",
    "agent_triggers",
    "agent_deployments",
    "agent_api_keys",
    "credential_defaults",
    "share_links",
)
M89_SCOPED = (
    "daily_spend_user",
    "daily_spend_agent",
    "daily_spend_model",
    "conversation_runs",
    "token_usages",
    "audit_events",
)
TENANT_ONLY = ("models", "system_llm_settings", "audit_events")
NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
}


def add_org_pair(table: str, *, tenant_exists: bool = False) -> None:
    with op.batch_alter_table(table, naming_convention=NAMING) as batch:
        batch.add_column(sa.Column("org_id", sa.Uuid(), nullable=True))
        if not tenant_exists:
            batch.add_column(sa.Column("tenant_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_tenant", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT"
            )
        batch.create_foreign_key(
            f"fk_{table}_org_tenant",
            "organizations",
            ["org_id", "tenant_id"],
            ["id", "tenant_id"],
            ondelete="RESTRICT",
        )


def drop_org_pair(table: str, *, keep_tenant: bool = False) -> None:
    with op.batch_alter_table(table, naming_convention=NAMING) as batch:
        batch.drop_constraint(f"fk_{table}_org_tenant", type_="foreignkey")
        batch.drop_column("org_id")
        if not keep_tenant:
            batch.drop_constraint(f"fk_{table}_tenant", type_="foreignkey")
            batch.drop_column("tenant_id")
