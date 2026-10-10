"""A-13: tenant-owned domain verification and enterprise identities."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from schema_drafts.org_authz.base import DraftBase, Identity, Timestamps


class TenantDomain(Identity, Timestamps, DraftBase):
    __tablename__ = "tenant_domains"
    __table_args__ = (
        CheckConstraint("status IN ('pending','verified','disabled')", name="status"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    txt_token: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SsoConnection(Identity, Timestamps, DraftBase):
    __tablename__ = "sso_connections"
    __table_args__ = (
        CheckConstraint("protocol IN ('oidc','ldap')", name="protocol"),
        CheckConstraint("status IN ('draft','tested','active','disabled')", name="status"),
        CheckConstraint(
            "default_role IN ('member','builder','admin','auditor')", name="default_role"
        ),
        CheckConstraint(
            "ldap_server_url IS NULL OR ldap_server_url LIKE 'ldaps://%' OR "
            "(ldap_server_url LIKE 'ldap://%' AND ldap_start_tls = true)",
            name="ldap_transport",
        ),
        CheckConstraint(
            "protocol <> 'ldap' OR status IN ('draft','disabled') OR "
            "(ldap_server_url IS NOT NULL AND ldap_bind_dn IS NOT NULL AND "
            "ldap_bind_password_encrypted IS NOT NULL AND key_id IS NOT NULL AND "
            "ldap_user_base_dn IS NOT NULL AND ldap_user_filter IS NOT NULL)",
            name="ldap_configuration",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    protocol: Mapped[str] = mapped_column(String(20), server_default="oidc")
    status: Mapped[str] = mapped_column(String(20), server_default="draft")
    issuer: Mapped[str] = mapped_column(String(1000))
    client_id: Mapped[str | None] = mapped_column(String(500))
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    ldap_server_url: Mapped[str | None] = mapped_column(String(1000))
    ldap_start_tls: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    ldap_bind_dn: Mapped[str | None] = mapped_column(String(1000))
    ldap_bind_password_encrypted: Mapped[str | None] = mapped_column(Text)
    ldap_user_base_dn: Mapped[str | None] = mapped_column(String(1000))
    ldap_user_filter: Mapped[str | None] = mapped_column(Text)
    ldap_group_base_dn: Mapped[str | None] = mapped_column(String(1000))
    ldap_group_filter: Mapped[str | None] = mapped_column(Text)
    ldap_attribute_mapping: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    ldap_ca_certificate: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str | None] = mapped_column(String(16))
    scopes: Mapped[list[str]] = mapped_column(JSON, server_default='["openid","email","profile"]')
    claim_mapping: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    group_role_mapping: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    jit_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    default_role: Mapped[str] = mapped_column(String(20), server_default="member")
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserIdentity(Identity, DraftBase):
    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Provenance only: current tenant acceptance is validated against the
    # connection used for this login, never against this historical pointer.
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sso_connections.id", ondelete="SET NULL")
    )
    issuer: Mapped[str] = mapped_column(String(1000))
    subject: Mapped[str] = mapped_column(String(255))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
