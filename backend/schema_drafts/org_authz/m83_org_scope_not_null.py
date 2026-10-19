"""m83 draft: scope constraints, restricted owners and preserved private histories."""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.scope_columns import M81_SCOPED, NAMING

revision = "m83_org_scope_not_null"
down_revision = "m82_default_org_backfill"
branch_labels = None
depends_on = None
SHARED = ("agents", "skills", "mcp_servers", "tools", "credentials", "marketplace_items")


def replace_fk(table: str, column: str, target: str, name: str, ondelete: str | None) -> None:
    matches = [
        fk
        for fk in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if fk["constrained_columns"] == [column]
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one source foreign key: {table}.{column}")
    old_name = matches[0]["name"] or f"fk_{table}_{column}_{target}"
    with op.batch_alter_table(table, naming_convention=NAMING) as batch:
        batch.drop_constraint(old_name, type_="foreignkey")
        batch.create_foreign_key(name, target, [column], ["id"], ondelete=ondelete)


def upgrade() -> None:
    for name in M81_SCOPED:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(name)}
        with op.batch_alter_table(name) as batch:
            if "is_system" in columns:
                batch.create_check_constraint(
                    f"ck_{name}_org_required",
                    "is_system OR (org_id IS NOT NULL AND tenant_id IS NOT NULL)",
                )
                batch.create_check_constraint(
                    f"ck_{name}_org_tenant_pair", "org_id IS NULL OR tenant_id IS NOT NULL"
                )
            else:
                batch.alter_column("org_id", existing_type=sa.Uuid(), nullable=False)
                batch.alter_column("tenant_id", existing_type=sa.Uuid(), nullable=False)
    for name in SHARED:
        owner = "owner_user_id" if name == "marketplace_items" else "user_id"
        replace_fk(name, owner, "users", f"fk_{name}_owner_restrict", "RESTRICT")
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("agent_name_snapshot", sa.String(100), nullable=True))
        batch.add_column(sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            "UPDATE conversations SET agent_name_snapshot = (SELECT name FROM agents "
            "WHERE agents.id = conversations.agent_id), "
            "last_activity_at = COALESCE(updated_at, created_at)"
        )
    )
    replace_fk(
        "conversations", "agent_id", "agents", "fk_conversations_agent_preserved", "SET NULL"
    )
    with op.batch_alter_table("conversations") as batch:
        batch.alter_column("agent_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column("agent_name_snapshot", existing_type=sa.String(100), nullable=False)
        batch.alter_column(
            "last_activity_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch.create_index("ix_conversations_last_activity", ["org_id", "last_activity_at"])


def downgrade() -> None:
    orphaned = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM conversations WHERE agent_id IS NULL")
    )
    if orphaned:
        raise ValueError("Cannot downgrade preserved histories without restoring their agents")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_index("ix_conversations_last_activity")
        batch.alter_column("agent_id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=True)
        batch.drop_column("agent_name_snapshot")
        batch.drop_column("last_activity_at")
    replace_fk("conversations", "agent_id", "agents", "fk_conversations_agent_legacy", None)
    for name in reversed(SHARED):
        owner = "owner_user_id" if name == "marketplace_items" else "user_id"
        replace_fk(
            name,
            owner,
            "users",
            f"fk_{name}_owner_legacy",
            "SET NULL" if name == "marketplace_items" else "CASCADE",
        )
    for name in reversed(M81_SCOPED):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(name)}
        with op.batch_alter_table(name) as batch:
            if "is_system" in columns:
                batch.drop_constraint(f"ck_{name}_org_required", type_="check")
                batch.drop_constraint(f"ck_{name}_org_tenant_pair", type_="check")
            else:
                batch.alter_column("org_id", existing_type=sa.Uuid(), nullable=True)
                batch.alter_column("tenant_id", existing_type=sa.Uuid(), nullable=True)
