"""Billable auxiliary request retained with its run when the uploaded image is removed."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ImageDescriptionCharge(Base):
    __tablename__ = "image_description_charges"
    __table_args__ = (
        CheckConstraint(
            "credential_scope IN ('tenant', 'personal') AND input_rate >= 0 AND output_rate >= 0",
            name="ck_image_description_charges_pricing",
        ),
    )

    # Shares its identity with the receipt and, once metered, the TokenUsage posting.
    # An upload deletion removes the caption and its receipt, never this billing claim.
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[UUID] = mapped_column()
    model_name: Mapped[str] = mapped_column(String(200))
    credential_scope: Mapped[str] = mapped_column(String(16))
    input_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    output_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    # Only the holder that won the reservation can settle this call. Cleared on settlement.
    settlement_digest: Mapped[str | None] = mapped_column(String(64))
