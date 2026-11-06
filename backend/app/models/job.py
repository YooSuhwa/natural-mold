"""Generic background jobs (feature plan v2 순번 5, 공통 기반; plan §5.13 ``jobs``).

Long work that does not belong to a chat run (document ingestion, re-indexing, bulk
tasks) is recorded here and run by the separate ``worker`` process (``app.jobs.worker``).
A worker claims a row with ``FOR UPDATE SKIP LOCKED``, keeps a lease alive with
heartbeats, and a row whose lease ran out goes back to the queue (``app.jobs.service``).
Every row belongs to one company and organization (RLS ``DIRECT_TENANT``); the worker
runs the job's own work in that company's session only.
"""

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps, org_fk

JOB_STATES: Final = ("queued", "running", "succeeded", "failed", "canceled")
TERMINAL_STATES: Final = frozenset({"succeeded", "failed", "canceled"})


class Job(Identity, Timestamps, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_jobs"),
        org_fk("jobs", "CASCADE"),
        CheckConstraint(
            "state IN ('queued','running','succeeded','failed','canceled')",
            name="ck_jobs_state",
        ),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_jobs_progress"),
        # One live job per (company, type, key): enqueueing the same key returns it.
        UniqueConstraint("tenant_id", "type", "idempotency_key", name="uq_jobs_idempotency"),
        Index("ix_jobs_claim", "state", "run_after"),
        Index("ix_jobs_tenant_resource", "tenant_id", "type", "resource_id"),
    )
    # Composite organization FK (``org_fk``), as every organization-scoped table.
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    # Who asked for it; the job runs as this person (null: a system job of the company).
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_jobs_actor_id_users")
    )
    type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_note: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobWorker(Base):
    """One running worker process and its last heartbeat (platform row, RLS ``PLATFORM_ONLY``).

    The operator's system status reads it to tell whether jobs are being picked up.
    """

    __tablename__ = "job_workers"
    __table_args__ = (PrimaryKeyConstraint("worker_id", name="pk_job_workers"),)
    worker_id: Mapped[str] = mapped_column(String(80))
    hostname: Mapped[str] = mapped_column(String(120), default="")
    job_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
