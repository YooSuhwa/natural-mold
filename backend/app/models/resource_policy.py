"""Company policies for one tool or MCP server (tool-governance-plan 3.4).

One row per resource key (``app.tools.keys``): whether the company allows the tool at all
(``enabled``), the approval mode it starts from (``default_mode``, with ``rule`` when that
is ``when``) and the modes an agent may pick (``allowed_modes``). A column left empty
follows the tool's risk level. A row keyed by an MCP server covers every tool of it; a
tool's own row wins field by field. ``org_id`` is empty for a company-wide row. Company
rows (RLS ``DIRECT_TENANT``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import JSON, ForeignKey, Index, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps

_TABLE = "resource_policies"


class ResourcePolicy(Identity, Timestamps, Base):
    __tablename__ = _TABLE
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_resource_policies"),
        Index(
            "uq_resource_policies_company_key",
            "tenant_id",
            "resource_key",
            unique=True,
            sqlite_where=text("org_id IS NULL"),
            postgresql_where=text("org_id IS NULL"),
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_resource_policies_tenant_id_tenants")
    )
    org_id: Mapped[UUID | None] = mapped_column(nullable=True)
    resource_key: Mapped[str] = mapped_column(String(120))
    # What the key meant when the row was saved, for people (a deleted tool keeps its name).
    resource_name: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool | None] = mapped_column(nullable=True)
    default_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    allowed_modes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    rule: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_resource_policies_updated_by_users")
    )


__all__ = ["ResourcePolicy"]
