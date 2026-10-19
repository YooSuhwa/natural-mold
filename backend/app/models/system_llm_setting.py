"""SystemLlmSetting ORM — operator-selected LLM model per role (ADR-019).

System features (Builder, Assistant, image generation) each map to a *role*
slot. Each slot stores the selected ``is_system`` credential (provider / api_key
/ base_url source) and the discovered ``model_name``. This replaces the former
``.env`` hardcoding (``builder_model_*`` / ``assistant_model_*`` / ``image_gen_*``).

One platform row per role plus at most one company override per (company, role):
m81 replaced ``UNIQUE(role)`` with two partial unique indexes (E-07). The set of
valid roles is pinned by a CHECK constraint. ``provider`` is *not* stored — it is
derived from ``credential.definition_key`` to keep a single source of truth.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Valid role slots. Kept in sync with the CHECK constraint below and the
# Alembic m45 seed rows.
SYSTEM_LLM_ROLES = ("text_primary", "text_fallback", "image")


class SystemLlmSetting(Base):
    __tablename__ = "system_llm_settings"
    __table_args__ = (
        CheckConstraint(
            "role IN ('text_primary', 'text_fallback', 'image')",
            name="ck_system_llm_settings_role",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_system_llm_settings_tenant", ondelete="RESTRICT"
        ),
        Index(
            "uq_system_llm_settings_platform_role",
            "role",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),
        Index(
            "uq_system_llm_settings_tenant_role",
            "tenant_id",
            "role",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
            sqlite_where=text("tenant_id IS NOT NULL"),
        ),
        # Tests rebuild the schema via ``Base.metadata.create_all``;
        # ``extend_existing`` keeps the import idempotent under that flow.
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    # NULL = platform default; set = this company's override (resolved company → platform).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Selected system credential. ``SET NULL`` on delete transitions the slot to
    # an unconfigured state (next system call raises SystemModelNotConfiguredError).
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    # Model identifier loaded via discover-models. NULL until the operator picks.
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
