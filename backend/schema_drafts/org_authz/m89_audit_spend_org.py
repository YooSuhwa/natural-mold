"""m89 draft: company/org cost keys, caller attribution and run reason codes."""

import sqlalchemy as sa

from alembic import op
from schema_drafts.org_authz.default_org_backfill import ORG, TENANT
from schema_drafts.org_authz.schema import schema_metadata
from schema_drafts.org_authz.scope_columns import M89_SCOPED, NAMING, add_org_pair, drop_org_pair

revision = "m89_audit_spend_org"
down_revision = "m88_requests_notifications"
branch_labels = None
depends_on = None
SPEND_AXES = (
    ("daily_spend_user", "user"),
    ("daily_spend_agent", "agent"),
    ("daily_spend_model", "model"),
)


def upgrade() -> None:
    for name in M89_SCOPED:
        add_org_pair(name, tenant_exists=name == "audit_events")
    with op.batch_alter_table("token_usages") as batch:
        batch.add_column(sa.Column("caller_user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_token_usages_caller", "users", ["caller_user_id"], ["id"], ondelete="SET NULL"
        )
    metadata = schema_metadata(later_columns=True)
    conversations = metadata.tables["conversations"]
    for name in ("conversation_runs", "token_usages"):
        table = metadata.tables[name]
        for column in ("org_id", "tenant_id"):
            value = (
                sa.select(conversations.c[column])
                .where(conversations.c.id == table.c.conversation_id)
                .scalar_subquery()
            )
            op.get_bind().execute(sa.update(table).values(**{column: value}))
        if name == "token_usages":
            owner = (
                sa.select(conversations.c.user_id)
                .where(conversations.c.id == table.c.conversation_id)
                .scalar_subquery()
            )
            op.get_bind().execute(sa.update(table).values(caller_user_id=owner))
        with op.batch_alter_table(name) as batch:
            batch.alter_column("org_id", existing_type=sa.Uuid(), nullable=False)
            batch.alter_column("tenant_id", existing_type=sa.Uuid(), nullable=False)
            batch.create_index(f"ix_{name}_org_created", ["org_id", "created_at"])
    for name, axis in SPEND_AXES:
        table = metadata.tables[name]
        op.get_bind().execute(sa.update(table).values(org_id=ORG, tenant_id=TENANT))
        with op.batch_alter_table(name, naming_convention=NAMING) as batch:
            batch.alter_column("org_id", existing_type=sa.Uuid(), nullable=False)
            batch.alter_column("tenant_id", existing_type=sa.Uuid(), nullable=False)
            batch.drop_constraint(f"uq_{name}_date_{axis}", type_="unique")
            batch.create_unique_constraint(
                f"uq_{name}_org_date_{axis}", ["org_id", "date", f"{axis}_id"]
            )
    audit = metadata.tables["audit_events"]
    # Pre-organization user audit rows belong to the installed default company.
    op.get_bind().execute(
        sa.update(audit)
        .where(audit.c.owner_user_id.is_not(None))
        .values(org_id=ORG, tenant_id=TENANT)
    )
    op.create_index("ix_audit_events_org_created", "audit_events", ["org_id", "created_at"])
    with op.batch_alter_table("agent_trigger_runs") as batch:
        batch.add_column(sa.Column("reason_code", sa.String(40), nullable=True))
        batch.create_check_constraint(
            "ck_agent_trigger_runs_reason",
            "reason_code IS NULL OR reason_code IN ('permission_revoked','auth_required',"
            "'delegation_broken','required_unavailable')",
        )


def downgrade() -> None:
    for name, axis in SPEND_AXES:
        table = sa.table(name, sa.column("date"), sa.column(f"{axis}_id", sa.Uuid()))
        grouped = (
            sa.select(table.c.date, table.c[f"{axis}_id"])
            .group_by(table.c.date, table.c[f"{axis}_id"])
            .having(sa.func.count() > 1)
            .subquery()
        )
        duplicates = op.get_bind().scalar(sa.select(sa.func.count()).select_from(grouped))
        if duplicates:
            raise ValueError(f"Cannot collapse multi-organization spend rows: {name}")
    with op.batch_alter_table("agent_trigger_runs") as batch:
        batch.drop_constraint("ck_agent_trigger_runs_reason", type_="check")
        batch.drop_column("reason_code")
    op.drop_index("ix_audit_events_org_created", table_name="audit_events")
    for name, axis in SPEND_AXES:
        with op.batch_alter_table(name) as batch:
            batch.drop_constraint(f"uq_{name}_org_date_{axis}", type_="unique")
            batch.create_unique_constraint(f"uq_{name}_date_{axis}", ["date", f"{axis}_id"])
    for name in ("token_usages", "conversation_runs"):
        op.drop_index(f"ix_{name}_org_created", table_name=name)
    with op.batch_alter_table("token_usages") as batch:
        batch.drop_constraint("fk_token_usages_caller", type_="foreignkey")
        batch.drop_column("caller_user_id")
    for name in reversed(M89_SCOPED):
        drop_org_pair(name, keep_tenant=name == "audit_events")
