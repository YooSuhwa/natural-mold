"""GuardrailModelSetting ORM — the operator's choice for the guardrail Korean model service.

One platform row (``id = 1``) or none. With no row the runtime uses the environment
(``GUARDRAIL_PII_MODEL_URL``, ``GUARDRAIL_PII_MODEL_API_KEY``); once the operator saves,
this row wins (``app/guardrails/model_settings.py``). The optional bearer key is a system
``http_bearer`` credential, so it is stored and rotated like every other secret.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

SINGLETON_ID = 1


class GuardrailModelSetting(Base):
    __tablename__ = "guardrail_model_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_guardrail_model_settings_singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=SINGLETON_ID)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
