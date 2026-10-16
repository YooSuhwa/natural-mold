"""C-02 review: same-key merge, system marketplace tenant tuples, archived-agent grant kinds."""

from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import JsonValue

from app.authz import tuple_rows as rows
from app.authz.tuples import TupleRuleError, expected_tuples
from tests.authz.test_tuples_expected import SNAPSHOT
from tests.authz.tuple_testkit import GRANTED, ID, NOW, model_violation, ref, spec, specs

ORG, O2, T, T2, U, A, R = (ID[key] for key in ("O", "O2", "T", "T2", "U", "A", "R"))


def _grant(number: int, resource_type: str, relation: str, principal_type: str, principal, **extra):
    base = rows.GrantRow(
        ID["GRANT"], ORG, resource_type, R, relation, principal_type, principal, GRANTED
    )
    return replace(base, id=type(ID["GRANT"])(int=0x900 + number), **extra)


def _snapshot(*grants: rows.GrantRow, **changes: object) -> rows.Snapshot:
    return replace(SNAPSHOT, grants=grants, **changes)  # type: ignore[arg-type]


def _key(produced, user: str, relation: str, obj: str):
    return [
        item
        for item in produced
        if (item.user, item.relation, item.object) == (user, relation, obj)
    ]


def _sources(*values: str) -> dict[str, JsonValue]:
    return {"allowed_sources": list(values)}


def test_unconditioned_grant_beats_an_expiring_one_for_the_same_key():
    produced = expected_tuples(
        _snapshot(
            _grant(1, "agent", "consumer", "user", U, resource_id=A),
            _grant(2, "agent", "consumer", "user", U, resource_id=A, expires_at=NOW + timedelta(1)),
        ),
        now=NOW,
    )
    assert _key(produced, ref("user", U), "consumer", ref("agent", A)) == [
        spec("user:U consumer agent:A")
    ]


def test_latest_expiry_wins():
    produced = expected_tuples(
        _snapshot(
            _grant(1, "agent", "consumer", "user", U, resource_id=A, expires_at=NOW + timedelta(1)),
            _grant(2, "agent", "consumer", "user", U, resource_id=A, expires_at=NOW + timedelta(9)),
        ),
        now=NOW,
    )
    (item,) = _key(produced, ref("user", U), "consumer", ref("agent", A))
    assert item.condition is not None
    assert (
        item.condition.context["grant_duration"]
        == f"{int((NOW + timedelta(9) - GRANTED).total_seconds())}s"
    )


def test_run_sources_are_unioned_in_canonical_order():
    mcp = ID["S"]
    produced = expected_tuples(
        _snapshot(
            _grant(
                1,
                "mcp_server",
                "consumer",
                "agent",
                A,
                resource_id=mcp,
                condition_name="allowed_run_source",
                condition_context=_sources("TRIGGER"),
            ),
            _grant(
                2,
                "mcp_server",
                "consumer",
                "agent",
                A,
                resource_id=mcp,
                condition_name="allowed_run_source",
                condition_context=_sources("CHAT"),
            ),
        ),  # fmt: skip
        now=NOW,
    )
    assert _key(produced, ref("agent", A), "consumer", ref("mcp_server", mcp)) == [
        spec(
            "agent:A consumer mcp_server:S",
            "allowed_run_source",
            allowed_sources=["CHAT", "TRIGGER"],
        )
    ]


def test_incompatible_conditions_for_one_key_are_rejected():
    cidr = {"cidr": "10.0.0.0/8"}
    grants = (
        _grant(1, "db_connection", "explorer", "user", U, condition_name="in_company_network",
               condition_context=cidr),
        _grant(2, "db_connection", "explorer", "user", U, condition_name="in_company_network",
               condition_context={"cidr": "192.168.0.0/16"}),
    )  # fmt: skip
    with pytest.raises(TupleRuleError, match="incompatible"):
        expected_tuples(_snapshot(*grants), now=NOW)


def test_system_marketplace_item_gets_one_tenant_tuple_per_fanout_tenant():
    item = rows.MarketplaceItemRow(R, None, None, None, is_system=True)
    fanout = [
        _grant(n, "marketplace_item", "installer", "organization", org, org_id=org,
               tenant_id=tenant, grant_kind="migration")
        for n, (org, tenant) in enumerate([(ORG, T), (O2, T2)])
    ]  # fmt: skip
    produced = expected_tuples(_snapshot(*fanout, marketplace_items=(item,)), now=NOW)
    on_item = {i for i in produced if i.object == ref("marketplace_item", R)}
    assert on_item == specs(
        "organization:O#member installer marketplace_item:R",
        "organization:O2#member installer marketplace_item:R",
        "tenant:T tenant marketplace_item:R",
        "tenant:T2 tenant marketplace_item:R",
    )
    assert [model_violation(i) for i in on_item] == [None] * len(on_item)


def test_archived_agent_keeps_grants_that_are_not_shares_or_delegations():
    archived = (rows.ResourceRow("agent", A, ORG, U, archived_at=NOW), *SNAPSHOT.resources[1:])
    grants = (
        _grant(1, "agent", "manager", "user", U, resource_id=A, grant_kind="migration"),
        _grant(2, "agent", "editor", "user", U, resource_id=A),
        _grant(3, "skill", "consumer", "agent", A, grant_kind="delegation"),
        _grant(4, "skill", "consumer", "agent", A, grant_kind="migration"),
    )
    produced = expected_tuples(_snapshot(*grants, resources=archived), now=NOW)
    grant_tuples = {i for i in produced if i.relation in {"manager", "editor", "consumer"}}
    assert grant_tuples >= specs("user:U manager agent:A", "agent:A consumer skill:R")
    assert spec("user:U editor agent:A") not in grant_tuples
    assert [i for i in grant_tuples if i.object == ref("skill", R)] == [
        spec("agent:A consumer skill:R")
    ]
