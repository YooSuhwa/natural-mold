"""Reconcile runs shown on the operator sync screen (plan 14.3, S27; H-04).

One row per reconcile run, whether an operator started it (``trigger='manual'``) or the
C-05 daily job did (``'scheduled'``). ``active`` is ``True`` while the run is queued or
running and ``NULL`` afterwards; its unique constraint (NULLs are distinct) admits one
live run at a time across processes. ``report`` keeps counts only, never tuple keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps

RUN_STATUSES: Final = ("queued", "running", "succeeded", "failed", "skipped")
RUN_TRIGGERS: Final = ("manual", "scheduled")
_TABLE: Final = "authz_reconcile_runs"


def _check(column: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ",".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{_TABLE}_{column}")


class AuthzReconcileRun(Identity, Timestamps, Base):
    __tablename__ = _TABLE
    __table_args__ = (
        _check("status", RUN_STATUSES),
        _check("trigger", RUN_TRIGGERS),
        CheckConstraint("active IS NULL OR active", name=f"ck_{_TABLE}_active"),
        UniqueConstraint("active", name=f"uq_{_TABLE}_active"),
        Index(f"ix_{_TABLE}_created_at", "created_at"),
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", name=f"fk_{_TABLE}_requested_by", ondelete="SET NULL")
    )
    partitions_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    partitions_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
