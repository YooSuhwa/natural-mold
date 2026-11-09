"""Ways to start a published workflow other than by hand (workflow-starts-sharing-plan 3.1-3.2).

* ``workflow_schedules``: a time or interval and fixed inputs. The person who made it is the
  runner. The leader's sweep claims a due row by moving ``next_run_at`` forward only if it is
  still the value it read, so one schedule fires once even with two leaders.
* ``workflow_webhooks``: an address (``public_id``) and a secret token (only its SHA-256 is
  kept) an outside system uses to start runs and read their results. Its maker is the runner.

Company rows (RLS ``DIRECT_TENANT``); both go with their workflow.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.resource_scope import RequiredOrgScope, resource_scope_constraints

SCHEDULE_KINDS: Final = ("interval", "cron", "once")
SCHEDULE_STATUSES: Final = ("active", "paused")
WEBHOOK_STATUSES: Final = ("active", "disabled")


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class WorkflowSchedule(RequiredOrgScope, Base):
    __tablename__ = "workflow_schedules"
    __table_args__ = (
        *resource_scope_constraints("workflow_schedules", system=False),
        CheckConstraint(_in("kind", SCHEDULE_KINDS), name="ck_workflow_schedules_kind"),
        CheckConstraint(_in("status", SCHEDULE_STATUSES), name="ck_workflow_schedules_status"),
        Index("ix_workflow_schedules_due", "status", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # interval: {"minutes": n}; cron: {"expression": "m h dom mon dow"}; once: {"at": ISO time}
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Seoul")
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    next_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # No foreign key: runs already point at their schedule (a cycle otherwise).
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_now_naive, onupdate=_now_naive, nullable=False
    )


class WorkflowWebhook(RequiredOrgScope, Base):
    __tablename__ = "workflow_webhooks"
    __table_args__ = (
        *resource_scope_constraints("workflow_webhooks", system=False),
        CheckConstraint(_in("status", WEBHOOK_STATUSES), name="ck_workflow_webhooks_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_now_naive, onupdate=_now_naive, nullable=False
    )


__all__ = [
    "SCHEDULE_KINDS",
    "SCHEDULE_STATUSES",
    "WEBHOOK_STATUSES",
    "WorkflowSchedule",
    "WorkflowWebhook",
]
