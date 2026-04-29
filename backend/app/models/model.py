"""LLM Model ORM — greenfield rewrite.

Drops ``api_key_encrypted`` (LLM API keys live in the new Credential domain
and are referenced via ``agents.llm_credential_id``) and ``provider_id``
(the ``llm_providers`` table is retired).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Model(Base):
    __tablename__ = "models"
    __table_args__ = {"extend_existing": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cost_per_input_token: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    cost_per_output_token: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_modalities: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    output_modalities: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    supports_vision: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_function_calling: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_reasoning: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Pricing source label — ``openrouter`` | ``litellm`` | ``manual``. Drives a
    # provenance badge in the UI and lets ``model_discovery`` tell apart
    # provider-supplied pricing from catalog-enriched fallbacks.
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
