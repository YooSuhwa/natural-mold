from __future__ import annotations

import uuid
from datetime import datetime, UTC

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.tool import agent_tools


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("models.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id"))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="agents")
    model: Mapped[Model] = relationship()
    tools: Mapped[list[Tool]] = relationship(secondary=agent_tools)
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
