"""Tool ORM — greenfield schema.

A ``Tool`` row is the *user's instance* of a :class:`ToolDefinition` from
``app/tools/registry.py``. It carries the user-supplied parameter values
(``parameters`` JSON) plus an optional FK to a ``Credential`` row. The
runtime resolves both via the registry; the row itself stores no logic.

Old columns (``type``, ``provider_name``, ``connection_id``,
``parameters_schema``, ``api_url``, ``http_method``, ``auth_type``,
``is_system``, ``tags``) are dropped here. The greenfield m13 migration
rebuilds the physical table with the new columns; tests use
``Base.metadata.create_all`` so the model is the source of truth.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentToolLink(Base):
    """Association object: agent <-> tool."""

    __tablename__ = "agent_tools"
    __table_args__ = {"extend_existing": True}

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tools.id", ondelete="CASCADE"),
        primary_key=True,
    )

    tool: Mapped[Tool] = relationship(lazy="joined")


class Tool(Base):
    __tablename__ = "tools"
    # ``is_system=True`` MUST imply ``user_id IS NULL`` (ADR-016 §4.4).
    # Enforced at the DB layer so a code bug cannot create a system tool that
    # leaks the operator's user_id into shared resources.
    __table_args__ = (
        CheckConstraint(
            "(is_system = false) OR (user_id IS NULL)",
            name="ck_tools_system_user_null",
        ),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # NULL = system-owned tool (visible to all users). Otherwise per-user instance.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )

    # Operator-managed tool (e.g. seeded prebuilt tools). Hidden from regular
    # user pickers; only super_user can manage.
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )

    # Logical FK to a registered ToolDefinition (``app.tools.registry``). Stored
    # as a string so registry rebuilds don't require a DB migration.
    definition_key: Mapped[str] = mapped_column(String(80), nullable=False)

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User-supplied parameter values keyed by FieldDef.name. Validated against
    # the definition's ``parameters`` list at create/update time.
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)

    # Optional credential — SET NULL on credential delete so the tool stays
    # configured but inactive until rebound.
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )

    # Eager-loadable relationship so chat_service can selectinload the
    # credential row alongside the tool without N+1 queries.
    credential: Mapped[Credential | None] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Credential", lazy="select"
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
