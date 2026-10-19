"""Explicit m81/m89 column contracts; all tenant/org FK pairs are named."""

import sqlalchemy as sa

from alembic import op
from app.migrations.org_authz_v1.contracts import NAMING


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
