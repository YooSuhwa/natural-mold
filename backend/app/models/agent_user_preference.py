"""Per-user agent preferences (plan 6.1; m86 part done early by D-02).

``agents.is_favorite`` stores the owner's favorite on the shared row and stays the
owner's source of truth until P8; viewers of a shared agent keep their own favorite
here, so one viewer's choice never reaches anybody else.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, PrimaryKeyConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentUserPreference(Base):
    __tablename__ = "agent_user_preferences"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "agent_id", name="pk_agent_user_preferences"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_agent_user_preferences_user_id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", name="fk_agent_user_preferences_agent_id", ondelete="CASCADE")
    )
    is_favorite: Mapped[bool] = mapped_column(default=False, server_default=false(), nullable=False)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
