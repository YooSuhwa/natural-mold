"""MCP server ORM — connection-less, credential-bound, status-tracked.

Holds the per-user configuration of a remote MCP endpoint: transport, URL or
launch command, credential reference, and the most recent connectivity status.
The actual tools exposed by the server are normalized into ``mcp_tools``
(see :mod:`app.models.mcp_tool`) so they can be enabled / disabled
independently and surfaced in the UI without re-querying the server.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship


class _Tx:
    """Transport identifiers — kept as plain string constants to stay portable
    across DB dialects (no enum table)."""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class _Status:
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    AUTH_NEEDED = "auth_needed"
    UNREACHABLE = "unreachable"
    DISABLED = "disabled"


from app.database import Base  # noqa: E402  — import after constants for clarity


class McpServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = {"extend_existing": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ``stdio`` | ``sse`` | ``streamable_http``
    transport: Mapped[str] = mapped_column(String(20), nullable=False)

    # Network transports (sse / streamable_http) populate ``url``; stdio uses
    # ``command`` + ``args``.
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)

    # Static configuration. Both fields support ``={{ $credentials.<field> }}``
    # interpolation that ``app.credentials.interpolation.resolve_deep`` resolves
    # at connect time using the linked credential's decrypted payload.
    env_vars: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    headers: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)

    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    # ``Credential`` ORM lives in app.models.credential — string ref avoids
    # an import cycle (Credential → User → … may grow new back-refs).
    credential: Mapped["Credential | None"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Credential", lazy="select", foreign_keys="[McpServer.credential_id]"
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=_Status.UNKNOWN
    )
    last_pinged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_tool_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )


# Re-export status / transport constants for callers that prefer named values.
TRANSPORTS = (_Tx.STDIO, _Tx.SSE, _Tx.STREAMABLE_HTTP)
STATUSES = (
    _Status.UNKNOWN,
    _Status.CONNECTED,
    _Status.AUTH_NEEDED,
    _Status.UNREACHABLE,
    _Status.DISABLED,
)
