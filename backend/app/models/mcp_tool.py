"""MCP tool ORM — per-server tool metadata cache.

Discovery (``app/mcp/discovery.py``) populates this table by calling
``list_tools`` on a connected MCP server. The unique constraint on
``(server_id, name)`` makes the discovery routine an idempotent upsert.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentMcpToolLink(Base):
    """Association object: agent <-> MCP tool.

    Created in m25 to complete the m5 follow-up — until now MCP tools
    couldn't be bound per-agent, so chat_service.build_tools_config skipped
    them entirely. With this link the unified Tools+Skills dialog can let
    users pick MCP tools alongside regular Tool rows.
    """

    __tablename__ = "agent_mcp_tools"
    __table_args__ = {"extend_existing": True}

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mcp_tool_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mcp_tools.id", ondelete="CASCADE"),
        primary_key=True,
    )

    mcp_tool: Mapped[McpTool] = relationship(lazy="joined")


class McpTool(Base):
    __tablename__ = "mcp_tools"
    __table_args__ = (
        UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    server: Mapped[McpServer] = relationship(  # type: ignore[name-defined]  # noqa: F821
        lazy="select"
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The remote tool's input JSON Schema as reported by ``list_tools``.
    input_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
