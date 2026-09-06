"""Trusted MCP Apps invocation provenance minted by the runtime."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class McpAppInvocationBinding(Base):
    """Immutable link between one real MCP tool call and its App resource."""

    __tablename__ = "mcp_app_invocation_bindings"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_mcp_app_binding_run_tool_call"),
        Index(
            "ix_mcp_app_binding_owner_run",
            "user_id",
            "conversation_id",
            "run_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    credential_subject_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mcp_server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    mcp_tool_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mcp_tools.id", ondelete="CASCADE"), nullable=False
    )
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    remote_tool_name: Mapped[str] = mapped_column(String(150), nullable=False)
    resource_uri: Mapped[str] = mapped_column(String(1_000), nullable=False)
    tool_meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    structured_content: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )


__all__ = ["McpAppInvocationBinding"]
