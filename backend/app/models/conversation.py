from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    is_pinned: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    # 메시지 idx → ISO timestamp 매핑. 한 번 부여되면 변경되지 않아
    # list_messages 호출 시 옛 메시지 시각이 흔들리지 않게 한다.
    message_timestamps: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # M-CHAT1b — user-selected branch tip (a LangGraph checkpoint_id). When
    # the user clicks `<` `>` on a sibling, the frontend stores the new tip
    # here so subsequent edits/regenerates fork off this branch instead of
    # the most-recent-by-time branch. ``None`` = use latest checkpoint.
    active_branch_checkpoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="conversations")  # type: ignore[name-defined]
