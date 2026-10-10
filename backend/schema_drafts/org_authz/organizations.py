"""A-13: company, organization and membership schemas (m78)."""

from datetime import datetime
from decimal import Decimal
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
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from schema_drafts.org_authz.base import Created, DraftBase, Identity, OrgScope, Timestamps, org_fk


class Tenant(Identity, Timestamps, DraftBase):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','deletion_scheduled')", name="status"),
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(32), server_default="active")
    limits: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    settings: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    secrets_namespace: Mapped[str] = mapped_column(String(250), unique=True)
    data_keys: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    deletion_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenantMember(Created, DraftBase):
    __tablename__ = "tenant_members"
    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','removed')", name="status"),
        Index("ix_tenant_members_user", "user_id", "status"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), server_default="active")


class TenantRoleGrant(Identity, Created, DraftBase):
    __tablename__ = "tenant_role_grants"
    __table_args__ = (
        CheckConstraint("role IN ('admin','auditor')", name="role"),
        UniqueConstraint("tenant_id", "user_id", "role"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    granted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Organization(Identity, Timestamps, DraftBase):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug"),
        UniqueConstraint("id", "tenant_id"),
        CheckConstraint("status IN ('active','suspended','archived')", name="status"),
        CheckConstraint("authz_epoch >= 0", name="authz_epoch"),
        CheckConstraint(
            "monthly_spend_limit IS NULL OR monthly_spend_limit >= 0", name="monthly_spend_limit"
        ),
        Index(
            "uq_organizations_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default = true"),
            sqlite_where=text("is_default = 1"),
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    settings: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    monthly_spend_limit: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    authz_epoch: Mapped[int] = mapped_column(Integer, server_default="0")
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class OrganizationMember(OrgScope, DraftBase):
    __tablename__ = "organization_members"
    __table_args__ = (
        org_fk("CASCADE"),
        CheckConstraint("status IN ('active','suspended','removed')", name="status"),
        CheckConstraint(
            "monthly_spend_limit IS NULL OR monthly_spend_limit >= 0", name="monthly_spend_limit"
        ),
        Index("ix_org_members_user", "user_id", "status"),
    )
    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    monthly_spend_limit: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    invited_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationRoleGrant(Identity, OrgScope, DraftBase):
    __tablename__ = "organization_role_grants"
    __table_args__ = (
        org_fk(),
        CheckConstraint("role IN ('admin','builder','auditor')", name="role"),
        CheckConstraint("principal_type IN ('user','group')", name="principal_type"),
        UniqueConstraint("org_id", "role", "principal_type", "principal_id"),
    )
    role: Mapped[str] = mapped_column(String(20))
    principal_type: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[UUID] = mapped_column()
    granted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
