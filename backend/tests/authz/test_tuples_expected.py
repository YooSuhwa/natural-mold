"""C-02: expected_tuples cross-row filters, determinism and model validity over a snapshot."""

import random
from dataclasses import fields, replace

from app.authz import tuple_rows as rows
from app.authz.tuples import expected_tuples
from tests.authz.tuple_testkit import GRANTED, ID, NOW, model_violation, ref, tup

T, T2, ORG, O2, U, U2 = (ID[key] for key in ("T", "T2", "O", "O2", "U", "U2"))
G, A, A2, R, S, M, GRANT = (ID[key] for key in ("G", "A", "A2", "R", "S", "M", "GRANT"))


def _grant(resource_type: str, relation: str, principal_type: str, principal, **extra):
    return rows.GrantRow(
        GRANT,
        extra.pop("org_id", ORG),
        resource_type,
        extra.pop("resource_id", R),
        relation,
        principal_type,
        principal,
        GRANTED,
        **extra,
    )


SNAPSHOT = rows.Snapshot(
    tenant_members=(rows.TenantMemberRow(T, U), rows.TenantMemberRow(T2, U2)),
    tenant_roles=(rows.TenantRoleRow(T, U, "admin"),),
    organizations=(rows.OrganizationRow(ORG, T), rows.OrganizationRow(O2, T2)),
    org_members=(rows.OrgMemberRow(ORG, U), rows.OrgMemberRow(O2, U2)),
    org_roles=(
        rows.OrgRoleRow(ORG, "admin", "user", U),
        rows.OrgRoleRow(ORG, "builder", "group", G),
    ),
    groups=(rows.GroupRow(G, ORG),),
    group_members=(rows.GroupMemberRow(G, ORG, U),),
    capabilities=(
        rows.CapabilityRow(ORG, "cap_create_agent", "organization", ORG),
        rows.CapabilityRow(ORG, "cap_create_skill", "user", U),
    ),
    resources=(
        rows.ResourceRow("agent", A, ORG, U),
        rows.ResourceRow("mcp_server", S, ORG, U),
        rows.ResourceRow("tool", R, None, None, is_system=True),
    ),
    credentials=(rows.CredentialRow(R, ORG, T, U, scope="tenant"),),
    marketplace_items=(rows.MarketplaceItemRow(R, ORG, T, U, tenant_publication="approved"),),
    mcp_tools=(rows.McpToolRow(R, S),),
    models=(rows.ModelRow(M),),
    grants=(
        _grant("tool", "allowed_org", "organization", ORG, grant_kind="system_fanout"),
        _grant("tool", "consumer", "organization", ORG, grant_kind="system_fanout"),
        _grant(
            "llm_model", "consumer", "organization", ORG, resource_id=M, grant_kind="system_fanout"
        ),
        _grant("agent", "viewer", "group", G, resource_id=A),
        _grant(
            "mcp_server",
            "consumer",
            "agent",
            A,
            resource_id=S,
            grant_kind="delegation",
            condition_name="allowed_run_source",
            condition_context={"allowed_sources": ["CHAT"]},
        ),  # fmt: skip
        _grant("agent", "consumer", "user", U, resource_id=A, expires_at=NOW + (NOW - GRANTED)),
    ),
)


def _without(snapshot: rows.Snapshot, **changes) -> rows.Snapshot:
    return replace(snapshot, **changes)


def test_full_snapshot_is_model_valid_sorted_and_deduplicated():
    produced = expected_tuples(SNAPSHOT, now=NOW)
    assert [model_violation(item) for item in produced if model_violation(item)] == []
    assert list(produced) == sorted(set(produced), key=lambda item: item.sort_key)
    assert len(produced) == 32  # 13 org facts + 13 resource rows + 6 grants


def test_input_order_and_duplicates_do_not_change_the_result():
    shuffled = {}
    rng = random.Random(7)  # noqa: S311 - deterministic shuffle, not security
    for field in fields(rows.Snapshot):
        values = list(getattr(SNAPSHOT, field.name)) * 2
        rng.shuffle(values)
        shuffled[field.name] = tuple(values)
    assert expected_tuples(rows.Snapshot(**shuffled), now=NOW) == expected_tuples(SNAPSHOT, now=NOW)


def test_suspended_member_loses_role_group_and_direct_grant_tuples():
    suspended = _without(SNAPSHOT, org_members=(rows.OrgMemberRow(ORG, U, "suspended"),))
    produced = expected_tuples(suspended, now=NOW)
    kept = {
        (item.relation, item.object.split(":")[0])
        for item in produced
        if item.user == ref("user", U)
    }
    # Ownership stays on the resource row (in_boundary refuses it); tenant facts are separate.
    assert kept == {
        ("owner", "agent"),
        ("owner", "mcp_server"),
        ("owner", "credential"),
        ("owner", "marketplace_item"),
        ("member", "tenant"),
        ("admin", "tenant"),
    }


def test_removed_tenant_member_loses_tenant_role():
    removed = _without(SNAPSHOT, tenant_members=(rows.TenantMemberRow(T, U, "removed"),))
    produced = expected_tuples(removed, now=NOW)
    assert tup(ref("user", U), "admin", ref("tenant", T)) not in produced


def test_cross_company_user_grant_is_not_projected():
    foreign = _without(SNAPSHOT, grants=(_grant("skill", "viewer", "user", U2),))
    assert tup(ref("user", U2), "viewer", ref("skill", R)) not in expected_tuples(foreign, now=NOW)


def test_cross_org_agent_delegation_is_not_projected():
    agents = (*SNAPSHOT.resources, rows.ResourceRow("agent", A2, O2, U2))
    delegation = _grant("skill", "consumer", "agent", A2, grant_kind="delegation")
    foreign = _without(SNAPSHOT, resources=agents, grants=(delegation,))
    assert tup(ref("agent", A2), "consumer", ref("skill", R)) not in expected_tuples(
        foreign, now=NOW
    )


def test_deleted_group_principal_projects_nothing():
    produced = expected_tuples(_without(SNAPSHOT, groups=()), now=NOW)
    assert not [
        item for item in produced if item.user.startswith("group:") or "group:" in item.object
    ]


def test_archived_agent_drops_shares_and_delegations_but_keeps_owner_org_resident():
    archived = (rows.ResourceRow("agent", A, ORG, U, archived_at=NOW), *SNAPSHOT.resources[1:])
    produced = expected_tuples(_without(SNAPSHOT, resources=archived), now=NOW)
    on_or_by_agent = {item for item in produced if ref("agent", A) in (item.object, item.user)}
    assert on_or_by_agent == {
        tup(ref("organization", ORG), "org", ref("agent", A)),
        tup(ref("agent", A), "resident_agent", ref("organization", ORG)),
        tup(ref("user", U), "owner", ref("agent", A)),
    }


def test_hidden_or_unknown_model_fanout_is_not_projected():
    for models in ((rows.ModelRow(M, is_visible=False),), ()):
        produced = expected_tuples(_without(SNAPSHOT, models=models), now=NOW)
        assert not [item for item in produced if item.object.startswith("llm_model:")]


def test_expired_grant_disappears_at_expiry():
    later = NOW + (NOW - GRANTED)
    produced = expected_tuples(SNAPSHOT, now=later)
    assert not [
        item for item in produced if item.condition and item.condition.name == "non_expired_grant"
    ]


def test_archived_org_and_suspended_tenant_keep_every_tuple():
    frozen = _without(
        SNAPSHOT,
        organizations=(
            rows.OrganizationRow(ORG, T, "archived"),
            rows.OrganizationRow(O2, T2, "suspended"),
        ),
    )
    assert expected_tuples(frozen, now=NOW) == expected_tuples(SNAPSHOT, now=NOW)
