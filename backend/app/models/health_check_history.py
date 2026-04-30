"""Time-series health check history for models and MCP servers.

A single table covers both target kinds — ``target_kind`` discriminates
``"model"`` vs ``"mcp_server"`` and ``target_id`` references the appropriate
parent row (no cross-table FK; the ``Tool``/``Model`` PK is independent).

The composite ``(target_kind, target_id, checked_at DESC)`` index is what
makes the "latest status" + "last N checks" queries cheap — without it the
"latest" lookup degrades to a sequential scan once the table grows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HealthCheckHistory(Base):
    __tablename__ = "health_check_history"
    __table_args__ = (
        Index(
            "ix_health_check_history_target_checked_at",
            "target_kind",
            "target_id",
            "checked_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # ``"model"`` or ``"mcp_server"`` — a literal-string discriminator (no
    # enum table) so the migration stays portable across PostgreSQL/SQLite.
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    # ``"healthy" | "unhealthy" | "degraded"`` — degraded is reserved for
    # MCP servers that respond but report ``auth_needed``.
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Bucketed error label so dashboards can group ("auth", "rate_limit", ...).
    error_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Whatever the underlying probe returned, kept for debug. Never surfaced
    # to the catalog list view; only available via the history detail endpoint.
    raw_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    checked_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )


__all__ = ["HealthCheckHistory"]
