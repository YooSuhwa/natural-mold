"""A-13: source grants, transactional projection and denial markers (m84)."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from schema_drafts.org_authz.base import Created, DraftBase, Identity, OrgScope, org_fk


class ResourceGrant(Identity, OrgScope, DraftBase):
    __tablename__ = "resource_grants"
    __table_args__ = (
        org_fk(),
        CheckConstraint("state IN ('active','revoking','revoked')", name="state"),
        CheckConstraint(
            "resource_type IN ('agent','skill','tool','mcp_server','mcp_tool','credential',"
            "'marketplace_item','knowledge_base','document','db_connection','db_query_tool',"
            "'workflow','llm_model')",
            name="resource_type",
        ),
        CheckConstraint(
            "relation IN ('consumer','viewer','editor','manager','installer','explorer',"
            "'blocked','allowed_org')",
            name="relation",
        ),
        CheckConstraint(
            "principal_type IN ('user','group','organization','agent')", name="principal_type"
        ),
        CheckConstraint(
            "relation != 'allowed_org' OR principal_type = 'organization'",
            name="allowed_org_principal",
        ),
        CheckConstraint(
            "grant_kind IN ('share','delegation','system_fanout','migration')", name="grant_kind"
        ),
        Index(
            "uq_resource_grants_active",
            "resource_type",
            "resource_id",
            "relation",
            "principal_type",
            "principal_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
        Index("ix_resource_grants_resource", "org_id", "resource_type", "resource_id"),
        Index("ix_resource_grants_principal", "principal_type", "principal_id"),
        Index("ix_resource_grants_delegator", "granted_by", "resource_type", "resource_id"),
    )
    state: Mapped[str] = mapped_column(String(20), server_default="active")
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[UUID] = mapped_column()
    relation: Mapped[str] = mapped_column(String(20))
    principal_type: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[UUID] = mapped_column()
    grant_kind: Mapped[str] = mapped_column(String(20), server_default="share")
    condition_name: Mapped[str | None] = mapped_column(String(80))
    condition_context: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_link: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)


class AuthzOutbox(Identity, Created, DraftBase):
    __tablename__ = "authz_outbox"
    __table_args__ = (
        CheckConstraint("op IN ('write','delete')", name="op"),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index(
            "ix_authz_outbox_unprocessed",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
            sqlite_where=text("processed_at IS NULL"),
        ),
    )
    op: Mapped[str] = mapped_column(String(10))
    tuple: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    source_table: Mapped[str] = mapped_column(String(80))
    source_id: Mapped[UUID] = mapped_column()
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthzRevocation(Identity, OrgScope, Created, DraftBase):
    __tablename__ = "authz_revocations"
    __table_args__ = (
        org_fk(),
        CheckConstraint(
            "scope IN ('grant','membership','group_member','role','capability',"
            "'ownership','resource')",
            name="scope",
        ),
        Index(
            "ix_authz_revocations_pending",
            "org_id",
            "subject",
            "object",
            postgresql_where=text("finalized_at IS NULL"),
            sqlite_where=text("finalized_at IS NULL"),
        ),
    )
    scope: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(100))
    object: Mapped[str] = mapped_column(String(100))
    fga_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthzShadowDiff(Identity, Created, DraftBase):
    __tablename__ = "authz_shadow_diffs"
    __table_args__ = (
        CheckConstraint("count > 0", name="count"),
        UniqueConstraint("route", "principal", "object", "permission", "legacy", "fga"),
    )
    route: Mapped[str] = mapped_column(String(255))
    principal: Mapped[str] = mapped_column(String(100))
    object: Mapped[str] = mapped_column(String(100))
    permission: Mapped[str] = mapped_column(String(80))
    legacy: Mapped[bool] = mapped_column(Boolean)
    fga: Mapped[bool] = mapped_column(Boolean)
    request_id: Mapped[str | None] = mapped_column(String(100))
    count: Mapped[int] = mapped_column(Integer, server_default="1")
