from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.agent import Agent


class AgentSubAgentLink(Base):
    """Association: agent <-> agent (self-referential, parent → sub).

    parent_agent_id 가 sub_agent_id 를 호출(위임) 가능. PK는 (parent, sub) 복합키
    이므로 같은 (parent, sub) link는 1번만 존재한다. position 으로 UI 정렬.
    """

    __tablename__ = "agent_subagents"
    __table_args__ = (
        CheckConstraint(
            "parent_agent_id != sub_agent_id",
            name="ck_agent_subagents_no_self",
        ),
        Index("ix_agent_subagents_parent_agent_id", "parent_agent_id"),
    )

    parent_agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sub_agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    # async 컨텍스트에서 link 단독 로딩 시점(예: cascade delete 검증, 단일 link 직접 조회)에
    # sub_agent 접근이 필요하므로 lazy="joined"가 안전. helpers/service의 명시 selectinload는
    # 부모 측 N+1 방지용이고, 여기 joined와는 다른 경로에서 작동한다.
    sub_agent: Mapped[Agent] = relationship(
        "Agent",
        foreign_keys=[sub_agent_id],
        lazy="joined",
    )
