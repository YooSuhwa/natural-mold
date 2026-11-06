"""Emergency stops (feature plan v2 순번 13, chapter 17.4; ``app/approvals/emergency.py``).

One row per stop of an agent, of every agent of one person, or of one MCP server. While a
row is ``active`` nothing new runs under it (``is_stopped``); the stop itself cancels the
running runs, pauses the schedules, suspends the Agent API deployments and cancels the
pending approvals, and ``effects`` keeps what it touched so the screen can show the state
and a release can restore the deployments. Company rows (RLS ``DIRECT_TENANT``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps

STOP_SCOPES: Final = ("agent", "user", "mcp_server")
_TABLE = "emergency_stops"


class EmergencyStop(Identity, Timestamps, Base):
    __tablename__ = _TABLE
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_emergency_stops"),
        CheckConstraint(
            "scope_type IN ('agent', 'user', 'mcp_server')", name="ck_emergency_stops_scope"
        ),
        CheckConstraint("status IN ('active', 'released')", name="ck_emergency_stops_status"),
        # One active stop per target: a second stop of the same target is the same stop.
        Index(
            "uq_emergency_stops_active_scope",
            "tenant_id",
            "scope_type",
            "scope_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_emergency_stops_tenant_id_tenants")
    )
    org_id: Mapped[UUID | None] = mapped_column(nullable=True)
    scope_type: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[UUID] = mapped_column()
    scope_name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    reason: Mapped[str] = mapped_column(String(500))
    stopped_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_emergency_stops_stopped_by_users")
    )
    stopped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_emergency_stops_released_by_users")
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(500))
    effects: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
