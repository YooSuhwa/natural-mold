"""Durable claim for one authorized auxiliary model call on an immutable image."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.conversation_run import utc_now_naive

MAX_DESCRIPTION_CHARS = 8_000


class ChatImageDescription(Base):
    __tablename__ = "chat_image_descriptions"
    __table_args__ = (
        UniqueConstraint(
            "attachment_id",
            "conversation_id",
            "content_sha256",
            "question_sha256",
            "model_id",
            "policy_sha256",
            name="uq_chat_image_description_request",
        ),
        CheckConstraint(
            "(status = 'pending' AND description IS NULL AND failure_code IS NULL) OR "
            f"(status = 'completed' AND length(description) BETWEEN 1 AND {MAX_DESCRIPTION_CHARS} "
            "AND description IS NOT NULL AND failure_code IS NULL) OR "
            "(status = 'failed' AND description IS NULL AND failure_code IS NOT NULL)",
            name="ck_chat_image_description_result",
        ),
        CheckConstraint(
            "length(content_sha256) = 64 AND length(question_sha256) = 64 "
            "AND length(policy_sha256) = 64",
            name="ck_chat_image_description_hashes",
        ),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0) AND "
            "(estimated_cost_usd IS NULL OR estimated_cost_usd >= 0) AND "
            "(credential_scope IS NULL OR credential_scope IN ('personal', 'tenant'))",
            name="ck_chat_image_description_usage",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    attachment_id: Mapped[UUID] = mapped_column(
        ForeignKey("message_attachments.id", ondelete="CASCADE")
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="CASCADE"), index=True
    )
    # Snapshot identity survives removal of a configured model.
    model_id: Mapped[UUID] = mapped_column()
    content_sha256: Mapped[str] = mapped_column(String(64))
    question_sha256: Mapped[str] = mapped_column(String(64))
    policy_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))
    description: Mapped[str | None] = mapped_column(String(MAX_DESCRIPTION_CHARS))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    credential_scope: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
