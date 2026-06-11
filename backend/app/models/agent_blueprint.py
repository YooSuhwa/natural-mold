"""Installed Agent Blueprint model.

Marketplace Agent resources install here first. Runnable ``agents`` are
created later from an installed Blueprint, which keeps the Agent list free
from half-configured marketplace installs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AgentBlueprint(Base):
    __tablename__ = "agent_blueprints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    categories: Mapped[list | None] = mapped_column(JSON, nullable=True)

    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_bindings: Mapped[dict[str, str] | None] = mapped_column(
        JSON, nullable=True, default=dict
    )

    source_marketplace_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_items.id", ondelete="SET NULL"), nullable=True
    )
    source_marketplace_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_versions.id", ondelete="SET NULL"), nullable=True
    )
    origin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    origin_kind: Mapped[str] = mapped_column(
        String(40), nullable=False, default="imported_by_me"
    )

    install_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )
    is_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_agent_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
