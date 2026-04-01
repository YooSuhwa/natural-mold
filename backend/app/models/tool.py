from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, ForeignKey, JSON, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

agent_tools = Table(
    "agent_tools",
    Base.metadata,
    Column("agent_id", ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
    Column("tool_id", ForeignKey("tools.id", ondelete="CASCADE"), primary_key=True),
)


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    auth_config: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)

    tools: Mapped[list[Tool]] = relationship(back_populates="mcp_server", cascade="all, delete-orphan")


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mcp_servers.id"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parameters_schema: Mapped[dict | None] = mapped_column(JSON)
    api_url: Mapped[str | None] = mapped_column(String(500))
    http_method: Mapped[str | None] = mapped_column(String(10))
    auth_type: Mapped[str | None] = mapped_column(String(20))
    auth_config: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)

    mcp_server: Mapped[MCPServer | None] = relationship(back_populates="tools")
