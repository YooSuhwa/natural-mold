"""C-02: one table case per plan 7.2 non-grant row (positive and negative), each model-valid."""

import pytest

from app.authz import tuple_rows as rows
from app.authz.tuples import RelationTuple, tuples_for
from tests.authz.tuple_testkit import ID, NOW, model_violation, specs

T, ORG, U, G, A, R, S = (ID[key] for key in ("T", "O", "U", "G", "A", "R", "S"))

AGENT = (
    "organization:O org agent:A",
    "agent:A resident_agent organization:O",
    "user:U owner agent:A",
)
ITEM = (
    "organization:O org marketplace_item:R",
    "tenant:T tenant marketplace_item:R",
    "user:U owner marketplace_item:R",
)
SHARED_KINDS = ("skill", "tool", "mcp_server")
CASES: list[tuple[str, rows.Row, set[RelationTuple]]] = [
    # tenant_members active row -> user member tenant
    ("tenant_member_active", rows.TenantMemberRow(T, U), specs("user:U member tenant:T")),
    ("tenant_member_suspended", rows.TenantMemberRow(T, U, "suspended"), set()),
    ("tenant_member_removed", rows.TenantMemberRow(T, U, "removed"), set()),
    # tenant_role_grants -> user admin|auditor tenant
    ("tenant_role_admin", rows.TenantRoleRow(T, U, "admin"), specs("user:U admin tenant:T")),
    ("tenant_role_auditor", rows.TenantRoleRow(T, U, "auditor"), specs("user:U auditor tenant:T")),
    # organizations -> tenant tenant organization (archive keeps tuples, 7.2.1)
    ("organization", rows.OrganizationRow(ORG, T), specs("tenant:T tenant organization:O")),
    (
        "organization_archived_keeps_tuple",
        rows.OrganizationRow(ORG, T, "archived"),
        specs("tenant:T tenant organization:O"),
    ),
    # agents -> org, resident_agent (+ owner as a shared resource row)
    ("agent_row", rows.ResourceRow("agent", A, ORG, U), specs(*AGENT)),
    (
        "agent_archived_keeps_owner_org_resident",
        rows.ResourceRow("agent", A, ORG, U, archived_at=NOW),
        specs(*AGENT),
    ),
    # company-shared credential -> tenant tuple (+ org/owner; allowed orgs are grants)
    (
        "tenant_credential_row",
        rows.CredentialRow(R, ORG, T, U, scope="tenant"),
        specs(
            "tenant:T tenant credential:R",
            "organization:O org credential:R",
            "user:U owner credential:R",
        ),
    ),
    (
        "system_tenant_credential_row",
        rows.CredentialRow(R, None, T, None, scope="tenant", is_system=True),
        specs("tenant:T tenant credential:R"),
    ),
    # marketplace_items -> org, tenant, owner_user_id; company-public -> tenant#member installer
    ("marketplace_item_org_public", rows.MarketplaceItemRow(R, ORG, T, U), specs(*ITEM)),
    (
        "marketplace_item_company_public",
        rows.MarketplaceItemRow(R, ORG, T, U, tenant_publication="approved"),
        specs(*ITEM, "tenant:T#member installer marketplace_item:R"),
    ),
    (
        "marketplace_item_publication_pending",
        rows.MarketplaceItemRow(R, ORG, T, U, tenant_publication="pending"),
        specs(*ITEM),
    ),
    (
        "marketplace_item_publication_rejected",
        rows.MarketplaceItemRow(R, ORG, T, U, tenant_publication="rejected"),
        specs(*ITEM),
    ),
    # organization_members active row -> user member organization
    ("org_member_active", rows.OrgMemberRow(ORG, U), specs("user:U member organization:O")),
    ("org_member_suspended", rows.OrgMemberRow(ORG, U, "suspended"), set()),
    ("org_member_removed", rows.OrgMemberRow(ORG, U, "removed"), set()),
    # organization_role_grants -> user | group#member  admin|builder|auditor organization
    (
        "org_role_user_admin",
        rows.OrgRoleRow(ORG, "admin", "user", U),
        specs("user:U admin organization:O"),
    ),
    (
        "org_role_group_builder",
        rows.OrgRoleRow(ORG, "builder", "group", G),
        specs("group:G#member builder organization:O"),
    ),
    (
        "org_role_user_auditor",
        rows.OrgRoleRow(ORG, "auditor", "user", U),
        specs("user:U auditor organization:O"),
    ),
    # groups -> organization org group; group_members -> user member group
    ("group", rows.GroupRow(G, ORG), specs("organization:O org group:G")),
    ("group_member", rows.GroupMemberRow(G, ORG, U), specs("user:U member group:G")),
    # org_capability_grants -> user | group#member | organization#member  cap_* organization
    (
        "capability_user",
        rows.CapabilityRow(ORG, "cap_create_agent", "user", U),
        specs("user:U cap_create_agent organization:O"),
    ),
    (
        "capability_group",
        rows.CapabilityRow(ORG, "cap_share_org_wide", "group", G),
        specs("group:G#member cap_share_org_wide organization:O"),
    ),
    (
        "capability_organization",
        rows.CapabilityRow(ORG, "cap_publish_marketplace", "organization", ORG),
        specs("organization:O#member cap_publish_marketplace organization:O"),
    ),
    # shared resource rows -> org + owner (user_id); is_system rows -> nothing (fanout only)
    *[
        (
            f"shared_{kind}",
            rows.ResourceRow(kind, R, ORG, U),
            specs(f"organization:O org {kind}:R", f"user:U owner {kind}:R"),
        )
        for kind in SHARED_KINDS
    ],
    (
        "shared_personal_credential",
        rows.CredentialRow(R, ORG, T, U),
        specs("organization:O org credential:R", "user:U owner credential:R"),
    ),
    *[
        (f"system_{kind}", rows.ResourceRow(kind, R, None, U, is_system=True), set())
        for kind in SHARED_KINDS
    ],
    (
        "system_platform_credential",
        rows.CredentialRow(R, None, None, None, "platform", True),
        set(),
    ),
    ("system_marketplace_item", rows.MarketplaceItemRow(R, None, None, None, True), set()),
    # mcp_tools -> mcp_server server mcp_tool
    ("mcp_tool", rows.McpToolRow(R, S), specs("mcp_server:S server mcp_tool:R")),
]


@pytest.mark.parametrize(("row", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_row_maps_to_exact_tuple_set(row: rows.Row, expected: set[RelationTuple]):
    produced = tuples_for(row, now=NOW)
    assert set(produced) == expected
    assert len(produced) == len(expected)
    assert [model_violation(item) for item in produced] == [None] * len(produced)


def test_rule_tables_cover_every_row_kind():
    from tests.authz.test_tuples_grants import CASES as GRANT_CASES

    kinds = {type(row) for _, row, _ in (*CASES, *GRANT_CASES)}
    assert kinds == set(rows.Row.__args__)
