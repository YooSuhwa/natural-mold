"""Permanent company purge receipts (I-07).

Written in the transaction that deletes the company's rows, so it outlives the
``tenants`` row (no FK): ``status='rows_deleted'`` carries the post-commit cleanup
(session revocation, files) in ``cleanup`` until the cleanup finishes
(``completed``). A rerun of the purge job resumes every ``rows_deleted`` receipt.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Timestamps


class TenantPurge(Timestamps, Base):
    __tablename__ = "tenant_purges"
    __table_args__ = (
        CheckConstraint("status IN ('rows_deleted','completed')", name="ck_tenant_purges_status"),
        PrimaryKeyConstraint(name="pk_tenant_purges"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), server_default="rows_deleted")
    cleanup: Mapped[dict[str, Any]] = mapped_column(JSON, server_default="{}")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
