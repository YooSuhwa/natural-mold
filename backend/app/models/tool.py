from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.connection import Connection


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


class Tool(Base):
    __tablename__ = "tools"
    # m14 partial unique index를 ORM metadata로도 표현해 metadata 기반 schema 환경
    # (테스트 conftest의 `Base.metadata.create_all`)에서도 race 가드가 작동하게 한다.
    # `type='mcp'`인 행에만 적용 — PREBUILT/CUSTOM/BUILTIN은 다른 매니징 정책.
    # PostgreSQL/SQLite 둘 다 partial index 지원(SQLite 3.8+).
    __table_args__ = (
        Index(
            "uq_mcp_tools_user_connection_name",
            "user_id",
            "connection_id",
            "name",
            unique=True,
            postgresql_where=text("type = 'mcp'"),
            sqlite_where=text("type = 'mcp'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # PREBUILT 도구의 provider 식별자. per-user connection 조회 키
    # (user_id + type='prebuilt' + provider_name). MCP/CUSTOM/BUILTIN은 NULL.
    provider_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_system: Mapped[bool] = mapped_column(default=False, nullable=False)
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

    connection: Mapped[Connection | None] = relationship(foreign_keys=[connection_id])
