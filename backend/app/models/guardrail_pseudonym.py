"""A conversation's pseudonym map (guardrail-minimum-plan.md §3.3.1).

One row per token: ``<<PERSON_1>>`` stands for one value in one conversation, so the same
person keeps the same pseudonym across turns. The value is Cipher V2 encrypted; the
company-keyed digest finds an existing token without decrypting. This is the "additional
information" of pseudonymised data: kept apart, deleted with the conversation and company.
"""

from uuid import UUID

from sqlalchemy import ForeignKey, PrimaryKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Created, Identity


class GuardrailPseudonym(Identity, Created, Base):
    __tablename__ = "guardrail_pseudonyms"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_guardrail_pseudonyms"),
        UniqueConstraint("conversation_id", "token", name="uq_guardrail_pseudonyms_token"),
        UniqueConstraint("conversation_id", "value_digest", name="uq_guardrail_pseudonyms_value"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "tenants.id", ondelete="CASCADE", name="fk_guardrail_pseudonyms_tenant_id_tenants"
        )
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
            name="fk_guardrail_pseudonyms_conversation_id_conversations",
        )
    )
    token: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(40))
    value_digest: Mapped[str] = mapped_column(String(64))
    value_encrypted: Mapped[str] = mapped_column(Text)
