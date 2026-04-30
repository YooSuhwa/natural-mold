"""Per-scope default credential mapping (``user_id × scope_kind × scope_key``).

A scope describes "what kind of usage is this default for". For example, a
PREBUILT tool's provider name is a scope key with ``scope_kind = "prebuilt"``;
an LLM model display name is a scope key with ``scope_kind = "llm_model"``.

A partial unique index enforces "at most one default per (user, scope) tuple"
at the database layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CredentialDefault(Base):
    __tablename__ = "credential_defaults"
    __table_args__ = (
        Index(
            "uq_credential_defaults_user_scope",
            "user_id",
            "scope_kind",
            "scope_key",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scope_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(120), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
