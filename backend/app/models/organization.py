"""Tenant organization models promoted from the reviewed m78–m80 contract (organizations)."""

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
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Created, Identity, Timestamps, org_fk


class Tenant(Identity, Timestamps, Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','suspended','deletion_scheduled')", name="ck_tenants_status"
        ),
        PrimaryKeyConstraint(name="pk_tenants"),
        UniqueConstraint("slug", name="uq_tenants_slug"),
        UniqueConstraint("secrets_namespace", name="uq_tenants_secrets_namespace"),
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), server_default="active")
    limits: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    settings: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    secrets_namespace: Mapped[str] = mapped_column(String(250))
    data_keys: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    deletion_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenantMember(Created, Base):
    __tablename__ = "tenant_members"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','suspended','removed')", name="ck_tenant_members_status"
        ),
        Index("ix_tenant_members_user", "user_id", "status"),
        PrimaryKeyConstraint(name="pk_tenant_members"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_tenant_members_tenant_id_tenants"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_tenant_members_user_id_users"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(20), server_default="active")


class TenantRoleGrant(Identity, Created, Base):
    __tablename__ = "tenant_role_grants"
    __table_args__ = (
        CheckConstraint("role IN ('admin','auditor')", name="ck_tenant_role_grants_role"),
        UniqueConstraint("tenant_id", "user_id", "role", name="uq_tenant_role_grants_tenant_id"),
        PrimaryKeyConstraint(name="pk_tenant_role_grants"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_tenant_role_grants_tenant_id_tenants")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_tenant_role_grants_user_id_users")
    )
    role: Mapped[str] = mapped_column(String(20))
    granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_tenant_role_grants_granted_by_users")
    )


class Organization(Identity, Timestamps, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_organizations_tenant_id"),
        UniqueConstraint("id", "tenant_id", name="uq_organizations_id"),
        CheckConstraint(
            "status IN ('active','suspended','archived')", name="ck_organizations_status"
        ),
        CheckConstraint("authz_epoch >= 0", name="ck_organizations_authz_epoch"),
        CheckConstraint(
            "monthly_spend_limit IS NULL OR monthly_spend_limit >= 0",
            name="ck_organizations_monthly_spend_limit",
        ),
        Index(
            "uq_organizations_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default = true"),
            sqlite_where=text("is_default = 1"),
        ),
        PrimaryKeyConstraint(name="pk_organizations"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT", name="fk_organizations_tenant_id_tenants")
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    settings: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    monthly_spend_limit: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    authz_epoch: Mapped[int] = mapped_column(Integer, server_default="0")
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_organizations_created_by_users")
    )


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        org_fk("organization_members", "CASCADE"),
        CheckConstraint(
            "status IN ('active','suspended','removed')", name="ck_organization_members_status"
        ),
        CheckConstraint(
            "monthly_spend_limit IS NULL OR monthly_spend_limit >= 0",
            name="ck_organization_members_monthly_spend_limit",
        ),
        Index("ix_org_members_user", "user_id", "status"),
        PrimaryKeyConstraint(name="pk_organization_members"),
    )
    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_organization_members_user_id_users"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    monthly_spend_limit: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    invited_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_organization_members_invited_by_users")
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "tenants.id", ondelete="RESTRICT", name="fk_organization_members_tenant_id_tenants"
        ),
        nullable=False,
    )


class OrganizationRoleGrant(Identity, Base):
    __tablename__ = "organization_role_grants"
    __table_args__ = (
        org_fk("organization_role_grants"),
        CheckConstraint(
            "role IN ('admin','builder','auditor')", name="ck_organization_role_grants_role"
        ),
        CheckConstraint(
            "principal_type IN ('user','group')", name="ck_organization_role_grants_principal_type"
        ),
        UniqueConstraint(
            "org_id",
            "role",
            "principal_type",
            "principal_id",
            name="uq_organization_role_grants_org_id",
        ),
        PrimaryKeyConstraint(name="pk_organization_role_grants"),
    )
    role: Mapped[str] = mapped_column(String(20))
    principal_type: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[UUID] = mapped_column()
    granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_organization_role_grants_granted_by_users"
        )
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "tenants.id", ondelete="RESTRICT", name="fk_organization_role_grants_tenant_id_tenants"
        ),
        nullable=False,
    )
