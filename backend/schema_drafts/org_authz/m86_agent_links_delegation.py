"""m86 draft: attachment provenance and per-user preferences retain legacy fields."""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.migration_api import create_tables, drop_tables
from schema_drafts.org_authz.schema import schema_metadata

revision = "m86_agent_links_delegation"
down_revision = "m85_marketplace_acl_to_grants"
branch_labels = None
depends_on = None
LINKS = ("agent_tools", "agent_mcp_tools", "agent_skills", "agent_subagents")


def upgrade() -> None:
    for name in LINKS:
        with op.batch_alter_table(name) as batch:
            batch.add_column(
                sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false"))
            )
            batch.add_column(sa.Column("delegated_by", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                f"fk_{name}_delegated_by", "users", ["delegated_by"], ["id"], ondelete="SET NULL"
            )
            batch.add_column(
                sa.Column("link_status", sa.String(20), nullable=False, server_default="active")
            )
            batch.add_column(sa.Column("broken_reason", sa.Text(), nullable=True))
            batch.create_check_constraint(
                f"ck_{name}_link_status", "link_status IN ('active','broken')"
            )
        source = "parent_agent_id" if name == "agent_subagents" else "agent_id"
        links = sa.table(name, sa.column("delegated_by", sa.Uuid()), sa.column(source, sa.Uuid()))
        agents = sa.table("agents", sa.column("id", sa.Uuid()), sa.column("user_id", sa.Uuid()))
        owner = sa.select(agents.c.user_id).where(agents.c.id == links.c[source]).scalar_subquery()
        op.execute(
            sa.update(links).where(links.c.delegated_by.is_(None)).values(delegated_by=owner)
        )
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("llm_credential_delegated_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_agents_llm_credential_delegated_by",
            "users",
            ["llm_credential_delegated_by"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(
        sa.text(
            "UPDATE agents SET llm_credential_delegated_by = user_id "
            "WHERE llm_credential_id IS NOT NULL"
        )
    )
    create_tables("agent_user_preferences")
    metadata = schema_metadata()
    agents, preferences = metadata.tables["agents"], metadata.tables["agent_user_preferences"]
    rows = (
        op.get_bind().execute(sa.select(agents.c.user_id, agents.c.id, agents.c.is_favorite)).all()
    )
    if rows:
        op.get_bind().execute(
            sa.insert(preferences),
            [
                {"user_id": user, "agent_id": agent, "is_favorite": favorite}
                for user, agent, favorite in rows
            ],
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE agents SET is_favorite = COALESCE((SELECT is_favorite "
            "FROM agent_user_preferences WHERE agent_user_preferences.agent_id = agents.id "
            "AND agent_user_preferences.user_id = agents.user_id), is_favorite)"
        )
    )
    drop_tables("agent_user_preferences")
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint("fk_agents_llm_credential_delegated_by", type_="foreignkey")
        batch.drop_column("llm_credential_delegated_by")
    for name in reversed(LINKS):
        with op.batch_alter_table(name) as batch:
            batch.drop_constraint(f"fk_{name}_delegated_by", type_="foreignkey")
            batch.drop_constraint(f"ck_{name}_link_status", type_="check")
            for column in ("required", "delegated_by", "link_status", "broken_reason"):
                batch.drop_column(column)
