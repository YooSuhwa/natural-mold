"""Legacy LLMProvider model — scheduled for deletion in M5.

The greenfield m18 migration drops the underlying ``llm_providers`` table,
but the model file is kept on disk so that legacy services (``model_service``,
``provider_service``, ``model_discovery``) keep importing without an
ImportError until M5 deletes both. The relationship to ``Model`` was removed
because ``Model`` no longer carries ``provider_id`` — leaving the relationship
in place would break ORM mapper configuration at first use.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LLMProvider(Base):
    __tablename__ = "llm_providers"
    __table_args__ = {"extend_existing": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
