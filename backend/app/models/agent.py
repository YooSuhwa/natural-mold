from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink

if TYPE_CHECKING:
    from app.models.agent_subagent import AgentSubAgentLink


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("models.id"), nullable=False)
    # Optional FK to the Credential supplying the LLM API key. SET NULL on
    # credential delete so the agent stays editable but inactive until rebound.
    llm_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    is_favorite: Mapped[bool] = mapped_column(default=False, nullable=False)
    model_params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    middleware_configs: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True, default=list
    )
    opener_questions: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True, default=list
    )
    # Ordered fallback model ids (stringified UUIDs). Tried in sequence when
    # the primary ``model_id`` raises a transient/auth error during
    # ``create_chat_model_with_fallback``. ``None`` and ``[]`` both mean
    # "no fallback" — the runtime then surfaces the original error.
    model_fallback_list: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id"))
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="agents")  # type: ignore[name-defined]
    model: Mapped[Model] = relationship()  # type: ignore[name-defined]
    # The Credential providing the LLM API key. ``lazy='select'`` so callers
    # explicitly opt into the join via ``selectinload``.
    llm_credential: Mapped[Credential | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Credential", lazy="select", foreign_keys="[Agent.llm_credential_id]"
    )
    tool_links: Mapped[list[AgentToolLink]] = relationship(
        cascade="all, delete-orphan",
    )
    mcp_tool_links: Mapped[list[AgentMcpToolLink]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    skill_links: Mapped[list[AgentSkillLink]] = relationship(
        cascade="all, delete-orphan",
    )
    sub_agent_links: Mapped[list[AgentSubAgentLink]] = relationship(
        "AgentSubAgentLink",
        foreign_keys="[AgentSubAgentLink.parent_agent_id]",
        cascade="all, delete-orphan",
        order_by="AgentSubAgentLink.position",
        lazy="selectin",
    )
    conversations: Mapped[list[Conversation]] = relationship(  # type: ignore[name-defined]
        back_populates="agent", cascade="all, delete-orphan"
    )

    @property
    def tools(self) -> list[Tool]:  # type: ignore[name-defined]
        """Convenience property: list of Tool objects (backward compat)."""
        return [link.tool for link in self.tool_links]
