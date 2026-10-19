"""Model version storage contract, activated by m84 rather than CLI-created DDL."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, Column, DateTime, Index, MetaData, String, Table, Uuid, text

version_metadata = MetaData()
model_versions = Table(
    "authz_model_versions",
    version_metadata,
    Column("id", Uuid(), primary_key=True, default=uuid4),
    Column("store_id", String(26), nullable=False),
    Column("model_id", String(26), nullable=False, unique=True),
    Column("model_sha256", String(64), nullable=False),
    Column("is_active", Boolean(), nullable=False, server_default=text("false")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)
Index(
    "uq_authz_model_versions_active",
    model_versions.c.is_active,
    unique=True,
    postgresql_where=model_versions.c.is_active.is_(True),
    sqlite_where=model_versions.c.is_active.is_(True),
)


class ModelVersion(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    store_id: str
    model_id: str
    model_sha256: str
    is_active: bool
    created_at: datetime
