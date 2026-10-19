"""Share link ORM — public read-only access to a conversation.

A user creates a share link from their conversation; visitors can fetch the
conversation + messages without authentication using ``share_token``. Revoking
sets ``revoked_at`` (soft delete) so the URL invalidates immediately and the
row stays for audit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.database import Base
from app.models.resource_scope import RequiredOrgScope, resource_scope_constraints


class ShareLink(RequiredOrgScope, Base):
    __tablename__ = "share_links"
    __table_args__ = resource_scope_constraints("share_links", system=False)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # URL-safe random string surfaced as the public ``shareId``.
    share_token: Mapped[str] = mapped_column(String(48), unique=True, index=True, nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Authorization alias; the physical m77/m83 owner column remains created_by.
    user_id: Mapped[uuid.UUID] = synonym("created_by")
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    # Soft delete — public lookups filter on ``revoked_at IS NULL``.
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
