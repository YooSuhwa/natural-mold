"""Portable draft metadata and repeated column contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DraftBase(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
        }
    )


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class Created:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Timestamps(Created):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OrgScope:
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )


def org_fk(ondelete: Literal["RESTRICT", "CASCADE"] = "RESTRICT") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["org_id", "tenant_id"],
        ["organizations.id", "organizations.tenant_id"],
        ondelete=ondelete,
    )
