"""In-app notifications (plan 6.1 ``notifications``, 14.2, S25; m88 → ``h02_notifications``).

A row names its recipient (``user_id``), the kind, the target (type, id), the actor and
a payload holding only the target's name at write time plus small enum fields — never
content bodies. Organization rows carry ``org_id``; company-level rows (company admin
notifications) leave it NULL and keep ``tenant_id``. Reads re-check the recipient's
current access to the target (``notification_queries``).

Deviation from 6.1: ``tenant_id`` (company-level rows and the composite org FK),
``target_type``/``target_id``/``actor_id`` as columns (1-hour merge and the read-time
re-check), ``updated_at`` (merge time).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps, org_fk

NOTIFICATION_KINDS: Final = (
    "access_request.created",
    "access_request.decided",
    "grant.received",
    "trigger.blocked",
    "connection.owner_needed",
    "delegation.broken",
    "ownership.transferred",
    "org.invitation",
    "tenant.limit_warning",
    "tenant.suspended",
    "marketplace.publish_requested",
    "marketplace.publish_decided",
    "usage.limit_warning",
    "agent_memo.proposal",
)
KIND_CHECK: Final = "kind IN (" + ",".join(f"'{kind}'" for kind in NOTIFICATION_KINDS) + ")"
_TABLE = "notifications"


class Notification(Identity, Timestamps, Base):
    __tablename__ = _TABLE
    __table_args__ = (
        org_fk(_TABLE, "CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{_TABLE}_tenant_id", ondelete="CASCADE"
        ),
        CheckConstraint(KIND_CHECK, name=f"ck_{_TABLE}_kind"),
        Index(f"ix_{_TABLE}_user_read_created", "user_id", "read_at", "created_at"),
        Index(f"ix_{_TABLE}_user_kind_target", "user_id", "kind", "target_id"),
    )

    org_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name=f"fk_{_TABLE}_user_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[uuid.UUID | None] = mapped_column()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", name=f"fk_{_TABLE}_actor_id", ondelete="SET NULL")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    link: Mapped[str | None] = mapped_column(String(500))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["NOTIFICATION_KINDS", "Notification"]
