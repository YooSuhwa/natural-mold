"""Access requests (plan 6.1 ``access_requests``, 10.9, S18; m88 → ``h01_access_requests``).

A member who can see a resource (8.4 ``min_visible``) asks its managers for a higher
level; an approval creates the grant through the E-01 share path. One pending request per
(resource, requester, level): the partial unique index. ``expires_at`` is set to 30 days
after creation; pending rows past it are expired lazily on read/write (no sweep job here).

Deviation from 6.1: ``tenant_id`` (composite org FK, as every org-scoped table),
``granted_relation`` (the level an approver chose, may be lower), ``expires_at`` and
``updated_at`` are added.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps, org_fk

ACCESS_REQUEST_STATUSES: Final = ("pending", "approved", "denied", "cancelled", "expired")
REQUESTABLE_TYPES: Final = ("agent", "skill", "tool", "mcp_server")
_TABLE = "access_requests"


class AccessRequest(Identity, Timestamps, Base):
    __tablename__ = _TABLE
    __table_args__ = (
        org_fk(_TABLE, "CASCADE"),
        CheckConstraint(
            "status IN ('pending','approved','denied','cancelled','expired')",
            name=f"ck_{_TABLE}_status",
        ),
        CheckConstraint(
            "resource_type IN ('agent','skill','tool','mcp_server')",
            name=f"ck_{_TABLE}_resource_type",
        ),
        Index(
            f"uq_{_TABLE}_pending",
            "org_id",
            "resource_type",
            "resource_id",
            "requester_id",
            "relation",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index(f"ix_{_TABLE}_org_status_created", "org_id", "status", "created_at"),
        Index(f"ix_{_TABLE}_requester_created", "requester_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    relation: Mapped[str] = mapped_column(String(20), nullable=False)
    requester_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name=f"fk_{_TABLE}_requester_id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", name=f"fk_{_TABLE}_decided_by", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    granted_relation: Mapped[str | None] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["ACCESS_REQUEST_STATUSES", "REQUESTABLE_TYPES", "AccessRequest"]
