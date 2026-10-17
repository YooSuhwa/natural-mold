"""Delegation columns shared by the agent link tables (plan 8.x m86, 10.6, D14; F-01).

``agent_tools``, ``agent_mcp_tools``, ``agent_skills`` and ``agent_subagents`` record who
attached the resource (``delegated_by``, the delegator whose ``can_use`` the delegation
validator re-checks), whether the link is required for a run (``required``) and whether
the delegation still holds (``link_status`` active/broken with ``broken_reason``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, false
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

LINK_STATUSES = ("active", "broken")


class AgentLinkDelegation:
    required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    broken_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @declared_attr
    def delegated_by(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        name = f"fk_{cls.__tablename__}_delegated_by"  # type: ignore[attr-defined]
        return mapped_column(ForeignKey("users.id", name=name, ondelete="SET NULL"), nullable=True)

    @declared_attr
    def link_status(cls) -> Mapped[str]:  # noqa: N805
        check = CheckConstraint(
            "link_status IN ('active','broken')",
            name=f"ck_{cls.__tablename__}_link_status",  # type: ignore[attr-defined]
        )
        return mapped_column(
            String(20), check, default="active", server_default="active", nullable=False
        )
