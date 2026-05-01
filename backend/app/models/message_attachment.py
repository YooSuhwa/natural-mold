"""Message attachment ORM — file blobs attached to a chat message.

The actual bytes are stored on local disk under ``settings.upload_dir`` (or an
S3-compatible store in the future); this row carries the public URL used by
the frontend to render previews. ``message_id`` is the LangGraph message id
once the message is sent; before send the row is "orphan" (linked to the
conversation only) and reaped by a future cleanup job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessageAttachment(Base):
    __tablename__ = "message_attachments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    # Backed by LangGraph checkpoint id (no FK). Nullable until the user
    # actually sends the message that references this upload.
    message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
