"""Assemble complete review metadata without changing the live application Base."""

from sqlalchemy import MetaData

from app import models as _legacy_models  # noqa: F401 - register baseline tables
from app.authz.model_version import model_versions
from app.database import Base
from schema_drafts.org_authz import grants as _grants  # noqa: F401 - declare draft tables
from schema_drafts.org_authz import groups as _groups  # noqa: F401 - declare draft tables
from schema_drafts.org_authz import (
    interactions as _interactions,  # noqa: F401 - declare draft tables
)
from schema_drafts.org_authz import (
    organizations as _organizations,  # noqa: F401 - declare draft tables
)
from schema_drafts.org_authz import sso as _sso  # noqa: F401 - declare draft tables
from schema_drafts.org_authz.base import DraftBase
from schema_drafts.org_authz.legacy_contract import apply_later_columns, apply_m81

NEW_TABLES = frozenset(
    {
        "tenants",
        "tenant_members",
        "tenant_role_grants",
        "organizations",
        "organization_members",
        "organization_role_grants",
        "groups",
        "group_members",
        "org_capability_grants",
        "org_invitations",
        "tenant_domains",
        "sso_connections",
        "user_identities",
        "resource_grants",
        "authz_outbox",
        "authz_revocations",
        "authz_model_versions",
        "authz_shadow_diffs",
        "resource_credential_bindings",
        "access_requests",
        "notifications",
        "agent_user_preferences",
    }
)


def schema_metadata(*, scope_columns: bool = False, later_columns: bool = False) -> MetaData:
    """Copy baseline and new table definitions for portable migration tests."""
    metadata = MetaData(naming_convention=DraftBase.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    for table in DraftBase.metadata.tables.values():
        table.to_metadata(metadata)
    model_versions.to_metadata(metadata)
    if scope_columns or later_columns:
        apply_m81(metadata)
    if later_columns:
        apply_later_columns(metadata)
    return metadata
