"""Ordered draft revisions; only phase-approved revisions enter live Alembic versions."""

from collections.abc import Callable
from dataclasses import dataclass

from schema_drafts.org_authz import m78_tenants_organizations as m78
from schema_drafts.org_authz import m79_groups as m79
from schema_drafts.org_authz import m80_org_capabilities_invitations as m80
from schema_drafts.org_authz import m81_org_scope_columns as m81
from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz import m83_org_scope_not_null as m83
from schema_drafts.org_authz import m84_authz_core as m84
from schema_drafts.org_authz import m85_marketplace_acl_to_grants as m85
from schema_drafts.org_authz import m86_agent_links_delegation as m86
from schema_drafts.org_authz import m87_credential_modes as m87
from schema_drafts.org_authz import m88_requests_notifications as m88
from schema_drafts.org_authz import m89_audit_spend_org as m89
from schema_drafts.org_authz import m90_drop_legacy_authz as m90
from schema_drafts.org_authz import m91_drop_shadow_diffs as m91
from schema_drafts.org_authz import m92_tenant_rls as m92


@dataclass(frozen=True, slots=True)
class Draft:
    revision: str
    predecessor: str
    upgrade: Callable[[], None]
    downgrade: Callable[[], None]


DRAFTS = (
    Draft(m78.revision, m78.down_revision, m78.upgrade, m78.downgrade),
    Draft(m79.revision, m79.down_revision, m79.upgrade, m79.downgrade),
    Draft(m80.revision, m80.down_revision, m80.upgrade, m80.downgrade),
    Draft(m81.revision, m81.down_revision, m81.upgrade, m81.downgrade),
    Draft(m82.revision, m82.down_revision, m82.upgrade, m82.downgrade),
    Draft(m83.revision, m83.down_revision, m83.upgrade, m83.downgrade),
    Draft(m84.revision, m84.down_revision, m84.upgrade, m84.downgrade),
    Draft(m85.revision, m85.down_revision, m85.upgrade, m85.downgrade),
    Draft(m86.revision, m86.down_revision, m86.upgrade, m86.downgrade),
    Draft(m87.revision, m87.down_revision, m87.upgrade, m87.downgrade),
    Draft(m88.revision, m88.down_revision, m88.upgrade, m88.downgrade),
    Draft(m89.revision, m89.down_revision, m89.upgrade, m89.downgrade),
    Draft(m92.revision, m92.down_revision, m92.upgrade, m92.downgrade),
    Draft(m90.revision, m90.down_revision, m90.upgrade, m90.downgrade),
    Draft(m91.revision, m91.down_revision, m91.upgrade, m91.downgrade),
)
