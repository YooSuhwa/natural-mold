from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.resource_scope import RequiredOrgScope, resource_scope_constraints

if TYPE_CHECKING:
    from app.models.agent import Agent


class Conversation(RequiredOrgScope, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        *resource_scope_constraints("conversations", system=False),
        CheckConstraint(
            "agent_lifecycle_state IN ('active', 'archived', 'purged')",
            name="ck_conversations_agent_lifecycle_state",
        ),
        CheckConstraint(
            "(agent_lifecycle_state IN ('active', 'archived') "
            "AND agent_id IS NOT NULL AND agent_purged_at IS NULL) OR "
            "(agent_lifecycle_state = 'purged' "
            "AND agent_id IS NULL AND agent_purged_at IS NOT NULL)",
            name="ck_conversations_agent_lifecycle_binding",
        ),
        Index("ix_conversations_owner_org_updated", "user_id", "org_id", "updated_at"),
        Index("ix_conversations_last_activity", "org_id", "last_activity_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", name="fk_conversations_agent_preserved", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_conversations_owner", ondelete="CASCADE"),
        nullable=False,
    )
    agent_name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_lifecycle_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    agent_archived_at_snapshot: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agent_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="ui")
    side_chat_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, unique=True
    )
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
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_unread_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_activity_source: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    runtime_policy_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runtime_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_policy_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_policy_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    queue_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    queue_paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    agent: Mapped[Agent | None] = relationship(back_populates="conversations")
