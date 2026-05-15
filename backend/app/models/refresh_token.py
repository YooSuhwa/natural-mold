"""RefreshToken ORM — DB-backed JWT refresh whitelist.

ADR-016 §4.2 — refresh tokens are stored as SHA-256 hashes (never the JWT
itself). Rotation revokes the prior row and inserts a new one; replay
detection (already-revoked hash returning) triggers a sweep that revokes
**all** active refresh tokens for the user — force re-login.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex digest of the refresh JWT — 64 chars. Stored hashed so a DB
    # leak does not yield usable tokens (defense in depth alongside HttpOnly).
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # NULL = active. Rotated/revoked rows are kept so a replay attempt is
    # detectable (hash exists but ``revoked_at IS NOT NULL``).
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        default=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(  # type: ignore[name-defined]
        back_populates="refresh_tokens"
    )
