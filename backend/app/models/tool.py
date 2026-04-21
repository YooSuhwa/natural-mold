from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.connection import Connection
    from app.models.credential import Credential


class AgentToolLink(Base):
    """Association object: agent <-> tool."""

    __tablename__ = "agent_tools"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tools.id", ondelete="CASCADE"),
        primary_key=True,
    )

    tool: Mapped[Tool] = relationship(lazy="joined")


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    auth_config: Mapped[dict | None] = mapped_column(JSON)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    tools: Mapped[list[Tool]] = relationship(
        back_populates="mcp_server", cascade="all, delete-orphan"
    )
    credential: Mapped[Credential | None] = relationship(
        foreign_keys=[credential_id], lazy="joined"
    )


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # PREBUILT 도구의 provider 식별자. per-user connection 조회 키
    # (user_id + type='prebuilt' + provider_name). MCP/CUSTOM/BUILTIN은 NULL.
    provider_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_system: Mapped[bool] = mapped_column(default=False, nullable=False)
    # deprecated: M6.1에서 제거 예정 (옵션 D — PATCH /api/tools/{id} connection_id
    # 도입 후 mcp_servers 테이블과 함께 drop). 이관 기간 동안 MCP fallback 용도로 유지.
    mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mcp_servers.id"))
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parameters_schema: Mapped[dict | None] = mapped_column(JSON)
    api_url: Mapped[str | None] = mapped_column(String(500))
    http_method: Mapped[str | None] = mapped_column(String(10))
    auth_type: Mapped[str | None] = mapped_column(String(20))
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    mcp_server: Mapped[MCPServer | None] = relationship(back_populates="tools")
    connection: Mapped[Connection | None] = relationship(
        foreign_keys=[connection_id]
    )
