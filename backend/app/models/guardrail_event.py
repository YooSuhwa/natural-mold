"""Guardrail detection records (guardrail-minimum-plan.md §3.5).

One row per (check, stage, rule, entity, action): which rule fired, how often and what was
done, never the text or the value (``value_digests`` are the company-keyed HMACs the
``hash`` action uses, so the same value can be counted without being stored). Company rows
(RLS ``DIRECT_TENANT``) that cascade with the company and with the conversation.
"""

from uuid import UUID

from sqlalchemy import JSON, ForeignKey, Index, Integer, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Created, Identity


class GuardrailEvent(Identity, Created, Base):
    __tablename__ = "guardrail_events"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_guardrail_events"),
        Index("ix_guardrail_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_guardrail_events_conversation", "conversation_id"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_guardrail_events_tenant_id_tenants")
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
            name="fk_guardrail_events_conversation_id_conversations",
        )
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL", name="fk_guardrail_events_agent_id_agents")
    )
    run_id: Mapped[str | None] = mapped_column(String(64))
    template: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(20))
    rule: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(10))
    count: Mapped[int] = mapped_column(Integer)
    value_digests: Mapped[list[str]] = mapped_column(JSON, default=list)
