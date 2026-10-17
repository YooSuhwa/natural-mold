"""Per-user connections for ``per_user`` tools and MCP servers (plan 6.1, 11.3; m87).

Same shape as ``skill_credential_bindings``: the credential FK is RESTRICT (a live
connection is never dropped by a raw delete; the credential and resource delete paths
remove their connections explicitly, ``app.authz.credential_bindings``). The resource
is polymorphic (``resource_type`` + ``resource_id``), so it has no FK.

Deviation: the plan's unique key is (user_id, resource_type, resource_id); it also
carries ``org_id`` here because a platform tool is used from every organization and a
credential lives in exactly one, so a member of two organizations connects per org.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Identity, Timestamps, org_fk

BINDING_RESOURCE_TYPES: Final = ("tool", "mcp_server")


class ResourceCredentialBinding(Identity, Timestamps, Base):
    __tablename__ = "resource_credential_bindings"
    __table_args__ = (
        org_fk("resource_credential_bindings", "CASCADE"),
        CheckConstraint(
            "resource_type IN ('tool','mcp_server')",
            name="ck_resource_credential_bindings_resource_type",
        ),
        UniqueConstraint(
            "org_id",
            "user_id",
            "resource_type",
            "resource_id",
            name="uq_resource_credential_bindings_user_resource",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_resource_credential_bindings_user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "credentials.id",
            name="fk_resource_credential_bindings_credential_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )


__all__ = ["BINDING_RESOURCE_TYPES", "ResourceCredentialBinding"]
