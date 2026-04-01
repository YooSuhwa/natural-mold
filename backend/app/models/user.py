from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)

    agents: Mapped[list[Agent]] = relationship(back_populates="user", cascade="all, delete-orphan")
    creation_sessions: Mapped[list[AgentCreationSession]] = relationship(back_populates="user")


from app.models.agent import Agent  # noqa: E402
from app.models.agent_creation_session import AgentCreationSession  # noqa: E402
