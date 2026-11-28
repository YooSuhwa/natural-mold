"""Outbound research provider attempts reserved within one organization's billing month."""

from datetime import date
from uuid import UUID

from sqlalchemy import CheckConstraint, PrimaryKeyConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import org_fk


class ResearchSearchUsage(Base):
    __tablename__ = "research_search_usage"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "org_id", "month_start", name="pk_research_search_usage"),
        org_fk("research_search_usage", ondelete="CASCADE"),
        CheckConstraint("attempts >= 0", name="ck_research_search_usage_attempts"),
    )

    tenant_id: Mapped[UUID] = mapped_column()
    org_id: Mapped[UUID] = mapped_column()
    month_start: Mapped[date] = mapped_column()
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
