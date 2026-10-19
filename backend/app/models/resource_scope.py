"""Live mapped scope columns and the exact deployable m81/m83 FK/check contract."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Constraint


class NullableOrgScope:
    org_id: Mapped[UUID | None] = mapped_column(nullable=True)
    tenant_id: Mapped[UUID | None] = mapped_column(nullable=True)


class RequiredOrgScope:
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)


def resource_scope_constraints(table_name: str, *, system: bool) -> tuple[Constraint, ...]:
    constraints: tuple[Constraint, ...] = (
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{table_name}_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name=f"fk_{table_name}_org_tenant",
            ondelete="RESTRICT",
        ),
    )
    if system:
        return (
            *constraints,
            CheckConstraint(
                "is_system OR (org_id IS NOT NULL AND tenant_id IS NOT NULL)",
                name=f"ck_{table_name}_org_required",
            ),
            CheckConstraint(
                "org_id IS NULL OR tenant_id IS NOT NULL",
                name=f"ck_{table_name}_org_tenant_pair",
            ),
        )
    return constraints
