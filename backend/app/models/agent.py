from __future__ import annotations

import uuid
from datetime import datetime, UTC

from typing import Any

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.tool import AgentToolLink


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("models.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    is_favorite: Mapped[bool] = mapped_column(default=False, nullable=False)
    model_params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id"))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None), onupdate=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="agents")
    model: Mapped[Model] = relationship()
    tool_links: Mapped[list[AgentToolLink]] = relationship(
        cascade="all, delete-orphan",
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )

    @property
    def tools(self) -> list[Tool]:
        """Convenience property: list of Tool objects (backward compat)."""
        return [link.tool for link in self.tool_links]
