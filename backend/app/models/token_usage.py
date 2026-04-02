from __future__ import annotations

import uuid
from datetime import datetime, UTC
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TokenUsage(Base):
    __tablename__ = "token_usages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False)
