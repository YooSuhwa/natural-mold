"""C-01 authorization core: source grants, projection outbox, revocation markers (plan 6.1)."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Identity as SqlIdentity
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.functions import FunctionElement

from app.authz.model_version import model_versions
from app.database import Base
from app.models.organization_base import Created, Identity, org_fk

RESOURCE_TYPES = (
    "agent",
    "skill",
    "tool",
    "mcp_server",
    "mcp_tool",
    "credential",
    "marketplace_item",
    "knowledge_base",
    "document",
    "db_connection",
    "db_query_tool",
    "workflow",
    "llm_model",
)
RELATIONS = (
    "consumer",
    "viewer",
    "editor",
    "manager",
    "installer",
    "explorer",
    "blocked",
    "allowed_org",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({','.join(repr(value) for value in values)})"


def _tenant_fk(table_name: str) -> Mapped[UUID]:
    return mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT", name=f"fk_{table_name}_tenant_id_tenants"),
        nullable=False,
    )


def _actor_fk(table_name: str, column: str) -> Mapped[UUID | None]:
    return mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name=f"fk_{table_name}_{column}_users")
    )


class ResourceGrant(Identity, Base):
    """Share and delegation source of truth; owners stay on the resource row."""

    __tablename__ = "resource_grants"
    __table_args__ = (
        org_fk("resource_grants"),
        CheckConstraint(
            "state IN ('active','revoking','revoked')", name="ck_resource_grants_state"
        ),
        CheckConstraint(
            _in("resource_type", RESOURCE_TYPES), name="ck_resource_grants_resource_type"
        ),
        CheckConstraint(_in("relation", RELATIONS), name="ck_resource_grants_relation"),
        CheckConstraint(
            "principal_type IN ('user','group','organization','agent')",
            name="ck_resource_grants_principal_type",
        ),
        CheckConstraint(
            "relation <> 'allowed_org' OR principal_type = 'organization'",
            name="ck_resource_grants_allowed_org_principal",
        ),
        CheckConstraint(
            "grant_kind IN ('share','delegation','system_fanout','migration')",
            name="ck_resource_grants_grant_kind",
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
        PrimaryKeyConstraint(name="pk_resource_grants"),
    )
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = _tenant_fk("resource_grants")
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
    granted_by: Mapped[UUID | None] = _actor_fk("resource_grants", "granted_by")
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    revoked_by: Mapped[UUID | None] = _actor_fk("resource_grants", "revoked_by")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_link: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)


class _NextOutboxSeq(FunctionElement[int]):
    """INSERT-time queue position: the identity sequence on PostgreSQL (C-04 migration).

    SQLite has no identity for a non-key column; there ``max + 1`` runs inside the INSERT,
    which is serial because SQLite has one writer. The outbox inserts one row per statement.
    """

    type = BigInteger()
    inherit_cache = True


@compiles(_NextOutboxSeq)
def _next_seq_default(element: _NextOutboxSeq, compiler: SQLCompiler, **kw: object) -> str:
    return "(SELECT COALESCE(MAX(seq), 0) + 1 FROM authz_outbox)"


@compiles(_NextOutboxSeq, "postgresql")
def _next_seq_postgres(element: _NextOutboxSeq, compiler: SQLCompiler, **kw: object) -> str:
    return "nextval(pg_get_serial_sequence('authz_outbox', 'seq'))"


class AuthzOutbox(Identity, Created, Base):
    """Transactional OpenFGA projection queue; writes/deletes are idempotent downstream.

    ``seq`` orders rows by INSERT (taken while the writer holds its row locks), never by a
    clock; ``tuple_key`` (``user relation object``) indexes the per-key ordering check.
    """

    __tablename__ = "authz_outbox"
    __table_args__ = (
        CheckConstraint("op IN ('write','delete')", name="ck_authz_outbox_op"),
        CheckConstraint("attempts >= 0", name="ck_authz_outbox_attempts"),
        Index(
            "ix_authz_outbox_unprocessed",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
            sqlite_where=text("processed_at IS NULL"),
        ),
        Index("uq_authz_outbox_seq", "seq", unique=True),
        Index(
            "ix_authz_outbox_key_pending",
            "tuple_key",
            "seq",
            postgresql_where=text("processed_at IS NULL"),
            sqlite_where=text("processed_at IS NULL"),
        ),
        PrimaryKeyConstraint(name="pk_authz_outbox"),
    )
    seq: Mapped[int] = mapped_column(BigInteger, SqlIdentity(), default=_NextOutboxSeq())
    tuple_key: Mapped[str] = mapped_column(String(255))
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


class AuthzRevocation(Identity, Created, Base):
    """D12 revocation marker; an unfinalized marker makes related checks fail closed."""

    __tablename__ = "authz_revocations"
    __table_args__ = (
        org_fk("authz_revocations"),
        CheckConstraint(
            "scope IN ('grant','membership','group_member','role','capability',"
            "'ownership','resource')",
            name="ck_authz_revocations_scope",
        ),
        Index(
            "ix_authz_revocations_pending",
            "org_id",
            "subject",
            "object",
            postgresql_where=text("finalized_at IS NULL"),
            sqlite_where=text("finalized_at IS NULL"),
        ),
        PrimaryKeyConstraint(name="pk_authz_revocations"),
    )
    org_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = _tenant_fk("authz_revocations")
    scope: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(100))
    object: Mapped[str] = mapped_column(String(100))
    fga_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthzShadowDiff(Identity, Created, Base):
    """Shadow-mode legacy/OpenFGA disagreement, aggregated per key; dropped after cutover."""

    __tablename__ = "authz_shadow_diffs"
    __table_args__ = (
        CheckConstraint("count > 0", name="ck_authz_shadow_diffs_count"),
        UniqueConstraint(
            "route",
            "principal",
            "object",
            "permission",
            "legacy",
            "fga",
            name="uq_authz_shadow_diffs_key",
        ),
        PrimaryKeyConstraint(name="pk_authz_shadow_diffs"),
    )
    route: Mapped[str] = mapped_column(String(255))
    principal: Mapped[str] = mapped_column(String(100))
    object: Mapped[str] = mapped_column(String(100))
    permission: Mapped[str] = mapped_column(String(80))
    legacy: Mapped[bool] = mapped_column(Boolean)
    fga: Mapped[bool] = mapped_column(Boolean)
    request_id: Mapped[str | None] = mapped_column(String(100))
    count: Mapped[int] = mapped_column(Integer, server_default="1")


# The model-version contract lives with the bootstrap CLI; register it for create_all/autogen.
authz_model_versions = model_versions.to_metadata(Base.metadata)
