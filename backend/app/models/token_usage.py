from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.resource_scope import NullableOrgScope, resource_scope_constraints


def _user_fk() -> ForeignKey:
    return ForeignKey("users.id", ondelete="SET NULL")


class TokenUsage(NullableOrgScope, Base):
    __tablename__ = "token_usages"
    __table_args__ = (
        *resource_scope_constraints("token_usages", system=False),
        Index("ix_token_usages_org_created", "org_id", "created_at"),
        Index("ix_token_usages_agent_caller", "agent_id", "caller_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    # F-14 (11.10): copied from the run at write time (``tenancy.attribution``).
    caller_user_id: Mapped[uuid.UUID | None] = mapped_column(_user_fk(), nullable=True)
    resource_owner_user_id: Mapped[uuid.UUID | None] = mapped_column(_user_fk(), nullable=True)
    on_behalf_of_user_id: Mapped[uuid.UUID | None] = mapped_column(_user_fk(), nullable=True)
    credential_subject_user_id: Mapped[uuid.UUID | None] = mapped_column(_user_fk(), nullable=True)
    run_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    invoker_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
