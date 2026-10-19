"""m81 uniqueness changes with matching PostgreSQL and SQLite partial indexes."""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.scope_columns import M81_SCOPED, NAMING


def partial(name: str, table: str, columns: list[str], predicate: str) -> None:
    op.create_index(
        name,
        table,
        columns,
        unique=True,
        postgresql_where=sa.text(predicate),
        sqlite_where=sa.text(predicate),
    )


def upgrade_constraints() -> None:
    with op.batch_alter_table("skills", naming_convention=NAMING) as batch:
        batch.drop_constraint("uq_skills_user_slug", type_="unique")
        batch.create_unique_constraint("uq_skills_org_user_slug", ["org_id", "user_id", "slug"])
    op.drop_index("uq_credential_defaults_user_scope", table_name="credential_defaults")
    op.create_index(
        "uq_credential_defaults_org_user_scope",
        "credential_defaults",
        ["org_id", "user_id", "scope_kind", "scope_key"],
        unique=True,
    )
    existing = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("marketplace_items")
    }
    if "uq_marketplace_items_owner_slug" in existing:
        op.drop_index("uq_marketplace_items_owner_slug", table_name="marketplace_items")
    if "uq_marketplace_items_system_slug" not in existing:
        partial(
            "uq_marketplace_items_system_slug",
            "marketplace_items",
            ["resource_type", "slug"],
            "is_system = true",
        )
    partial(
        "uq_marketplace_items_org_owner_slug",
        "marketplace_items",
        ["org_id", "owner_user_id", "resource_type", "slug"],
        "owner_user_id IS NOT NULL",
    )
    role_constraints = [
        constraint
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints("system_llm_settings")
        if constraint["column_names"] == ["role"]
    ]
    if len(role_constraints) != 1:
        raise ValueError("Expected exactly one legacy system LLM role constraint")
    role_constraint = role_constraints[0]["name"] or "uq_system_llm_settings_role"
    with op.batch_alter_table("system_llm_settings", naming_convention=NAMING) as batch:
        batch.drop_constraint(role_constraint, type_="unique")
    partial(
        "uq_system_llm_settings_platform_role", "system_llm_settings", ["role"], "tenant_id IS NULL"
    )
    partial(
        "uq_system_llm_settings_tenant_role",
        "system_llm_settings",
        ["tenant_id", "role"],
        "tenant_id IS NOT NULL",
    )
    partial(
        "uq_models_tenant_default",
        "models",
        ["tenant_id"],
        "is_default = true AND tenant_id IS NOT NULL",
    )
    partial(
        "uq_models_platform_default",
        "models",
        ["is_default"],
        "is_default = true AND tenant_id IS NULL",
    )
    for name in M81_SCOPED:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(name)}
        owner = (
            "user_id"
            if "user_id" in columns
            else "owner_user_id"
            if "owner_user_id" in columns
            else "created_by"
        )
        op.create_index(f"ix_{name}_org_owner", name, ["org_id", owner])


def downgrade_constraints() -> None:
    for name in reversed(M81_SCOPED):
        op.drop_index(f"ix_{name}_org_owner", table_name=name)
    for name in ("uq_models_tenant_default", "uq_models_platform_default"):
        op.drop_index(name, table_name="models")
    for name in ("uq_system_llm_settings_platform_role", "uq_system_llm_settings_tenant_role"):
        op.drop_index(name, table_name="system_llm_settings")
    with op.batch_alter_table("system_llm_settings") as batch:
        batch.create_unique_constraint("uq_system_llm_settings_role", ["role"])
    op.drop_index("uq_marketplace_items_org_owner_slug", table_name="marketplace_items")
    partial(
        "uq_marketplace_items_owner_slug",
        "marketplace_items",
        ["owner_user_id", "resource_type", "slug"],
        "owner_user_id IS NOT NULL",
    )
    op.drop_index("uq_credential_defaults_org_user_scope", table_name="credential_defaults")
    op.create_index(
        "uq_credential_defaults_user_scope",
        "credential_defaults",
        ["user_id", "scope_kind", "scope_key"],
        unique=True,
    )
    with op.batch_alter_table("skills") as batch:
        batch.drop_constraint("uq_skills_org_user_slug", type_="unique")
        batch.create_unique_constraint("uq_skills_user_slug", ["user_id", "slug"])
