"""Association: agent → published workflow it may call as a tool in chat (workflow-tool-plan).

One row per (agent, workflow). The tool is built from the workflow's **current** published
version when a chat run starts (T3), and only when the person chatting may run it (T6).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.agent_link_delegation import AgentLinkDelegation


class AgentWorkflowLink(AgentLinkDelegation, Base):
    __tablename__ = "agent_workflows"
    __table_args__ = (Index("ix_agent_workflows_workflow_id", "workflow_id"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False
    )
