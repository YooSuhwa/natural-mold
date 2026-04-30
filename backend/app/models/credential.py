"""Credential ORM — greenfield schema for the new credential domain.

Stores a single Cipher V2 blob (``data_encrypted``) plus enough metadata to
list, test, and rotate credentials without decrypting them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Credential(Base):
    __tablename__ = "credentials"
    # The legacy m6~m12 schema reused this table; tests rebuild it via
    # ``Base.metadata.create_all``. ``extend_existing`` lets the import remain
    # idempotent under that flow.
    __table_args__ = {"extend_existing": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    definition_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Cipher V2 base64 blob (``[version 1B][salt 32B][authTag 16B][ciphertext]``).
    data_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # 8-char identifier of the active key when this row was last (re)encrypted.
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)
    # Cached list of field names present in ``data`` so list views avoid decrypt.
    field_keys: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)

    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # ``is_system=True`` rows are operator-managed keys (Fix Agent / builder
    # / image generation / future bootstrap flows). They never surface in
    # user-facing pickers — see ``credential_service.list_for_user`` and
    # the ``/api/system-credentials`` route family.
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_test_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
