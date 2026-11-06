"""Company guardrail templates, their published versions and agent bindings (plan §3.4).

* ``guardrail_templates``: a company's editable draft (rules as JSON) and its status.
* ``guardrail_template_versions``: what publishing froze; never edited, so a run pinned to
  a version and a policy pointing at a template both read the same rules.
* ``agent_guardrail_bindings``: one agent's choice: inherit the company default, a
  template, or off, plus per-rule overrides the company has not locked.

All company rows (RLS ``DIRECT_TENANT``) cascading with the company; a binding also goes
with its agent.
"""

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Created, Identity, Timestamps

TEMPLATE_STATUSES: Final = ("draft", "published", "archived")
BINDING_MODES: Final = ("inherit", "template", "off")


class GuardrailTemplate(Identity, Timestamps, Base):
    __tablename__ = "guardrail_templates"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_guardrail_templates"),
        CheckConstraint(
            "status IN ('draft','published','archived')", name="ck_guardrail_templates_status"
        ),
        UniqueConstraint("tenant_id", "name", name="uq_guardrail_templates_name"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "tenants.id", ondelete="CASCADE", name="fk_guardrail_templates_tenant_id_tenants"
        )
    )
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    draft_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_guardrail_templates_created_by_users")
    )


class GuardrailTemplateVersion(Identity, Created, Base):
    __tablename__ = "guardrail_template_versions"
    __table_args__ = (
        PrimaryKeyConstraint(name="pk_guardrail_template_versions"),
        UniqueConstraint("template_id", "version", name="uq_guardrail_template_versions_version"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
            name="fk_guardrail_template_versions_tenant_id_tenants",
        )
    )
    template_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "guardrail_templates.id",
            ondelete="CASCADE",
            name="fk_guardrail_template_versions_template_id_guardrail_templates",
        )
    )
    version: Mapped[int] = mapped_column(Integer)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_guardrail_template_versions_published_by_users",
        )
    )


class AgentGuardrailBinding(Base):
    __tablename__ = "agent_guardrail_bindings"
    __table_args__ = (
        PrimaryKeyConstraint("agent_id", name="pk_agent_guardrail_bindings"),
        CheckConstraint(
            "mode IN ('inherit','template','off')", name="ck_agent_guardrail_bindings_mode"
        ),
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "agents.id", ondelete="CASCADE", name="fk_agent_guardrail_bindings_agent_id_agents"
        )
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "tenants.id", ondelete="CASCADE", name="fk_agent_guardrail_bindings_tenant_id_tenants"
        )
    )
    mode: Mapped[str] = mapped_column(String(20), default="inherit")
    template_ref: Mapped[str | None] = mapped_column(String(80))
    # {rule_id: {"enabled": bool, "action": str}}; locked rules ignore it.
    rule_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_agent_guardrail_bindings_updated_by_users"
        )
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
