"""Company export jobs (plan S04 "내보내기", 9.7 ``/api/admin/tenants/{id}/export|exports``; I-07).

One row per operator export request. ``storage_path`` is relative to ``data_root``
(ADR-018) and points at the finished zip; the archive is downloadable until
``expires_at`` (7 days). At most one queued/running job per company (partial unique
index). Rows cascade with the company and are removed by the permanent purge.
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps

EXPORT_STATUSES: Final = ("queued", "running", "succeeded", "failed")
_ACTIVE: Final = "status IN ('queued','running')"


class TenantExport(Identity, Timestamps, Base):
    __tablename__ = "tenant_exports"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ",".join(f"'{status}'" for status in EXPORT_STATUSES) + ")",
            name="ck_tenant_exports_status",
        ),
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_tenant_exports_progress"),
        PrimaryKeyConstraint(name="pk_tenant_exports"),
        Index("ix_tenant_exports_tenant_created", "tenant_id", "created_at"),
        Index(
            "uq_tenant_exports_active",
            "tenant_id",
            unique=True,
            postgresql_where=text(_ACTIVE),
            sqlite_where=text(_ACTIVE),
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_tenant_exports_tenant_id_tenants")
    )
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_tenant_exports_requested_by_users")
    )
    status: Mapped[str] = mapped_column(String(20), server_default="queued")
    progress: Mapped[int] = mapped_column(Integer, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64))
    storage_path: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
