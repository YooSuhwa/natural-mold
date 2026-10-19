"""Repeated organization model columns, with explicit stable constraint names."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, func
from sqlalchemy.orm import Mapped, mapped_column


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class Created:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Timestamps(Created):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def org_fk(
    table_name: str, ondelete: Literal["RESTRICT", "CASCADE"] = "RESTRICT"
) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["org_id", "tenant_id"],
        ["organizations.id", "organizations.tenant_id"],
        name=f"fk_{table_name}_org_id_organizations",
        ondelete=ondelete,
    )
