"""C-02: plan 7.2 resource_grants rows, including system/model/credential/marketplace fanout."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import JsonValue

from app.authz import tuple_rows as rows
from app.authz.tuples import RelationTuple, TupleRuleError, tuples_for
from tests.authz.tuple_testkit import GRANTED, ID, NOW, model_violation, spec, specs

ORG, U, G, A, A2, M = (ID[key] for key in ("O", "U", "G", "A", "A2", "M"))


def grant(
    resource_type: str, relation: str, principal_type: str, principal: UUID, **extra: object
) -> rows.GrantRow:
    base = rows.GrantRow(
        ID["GRANT"], ORG, resource_type, ID["R"], relation, principal_type, principal, GRANTED
    )
    return replace(base, **extra)


def fanout(resource_type: str, relation: str = "consumer", **extra: object) -> rows.GrantRow:
    return grant(resource_type, relation, "organization", ORG, grant_kind="system_fanout", **extra)


IN_30_DAYS = GRANTED + timedelta(days=30)
EXPIRY = {"grant_time": "2026-10-01T00:00:00Z", "grant_duration": "2592000s"}
CHAT_ONLY: dict[str, JsonValue] = {"allowed_sources": ["CHAT"]}
CASES: list[tuple[str, rows.Row, set[RelationTuple]]] = [
    # resource_grants state=active -> principal relation resource
    ("user_viewer", grant("skill", "viewer", "user", U), specs("user:U viewer skill:R")),
    ("group_editor", grant("tool", "editor", "group", G), specs("group:G#member editor tool:R")),
    ("user_manager", grant("agent", "manager", "user", U), specs("user:U manager agent:R")),
    (
        "org_wide_agent_consumer",
        grant("agent", "consumer", "organization", ORG),
        specs("organization:O#member consumer agent:R"),
    ),
    (
        "subagent_delegation",
        grant("agent", "consumer", "agent", A, grant_kind="delegation"),
        specs("agent:A consumer agent:R"),
    ),
    (
        "mcp_tool_blocked",
        grant("mcp_tool", "blocked", "agent", A2),
        specs("agent:A2 blocked mcp_tool:R"),
    ),
    (
        "restricted_item_user",
        grant("marketplace_item", "installer", "user", U),
        specs("user:U installer marketplace_item:R"),
    ),
    ("revoking", grant("skill", "viewer", "user", U, state="revoking"), set()),
    ("revoked", grant("skill", "viewer", "user", U, state="revoked"), set()),
    # expires_at -> non_expired_grant(grant_time, grant_duration); expired -> nothing
    ("expired_at_now", grant("agent", "consumer", "user", U, expires_at=NOW), set()),
    (
        "expiring",
        grant("agent", "consumer", "user", U, expires_at=IN_30_DAYS),
        {spec("user:U consumer agent:R", "non_expired_grant", **EXPIRY)},
    ),
    # MCP server agent delegation always carries allowed_run_source (default: every source)
    (
        "mcp_delegation_chat_only",
        grant(
            "mcp_server",
            "consumer",
            "agent",
            A,
            grant_kind="delegation",
            condition_name="allowed_run_source",
            condition_context=CHAT_ONLY,
        ),
        {spec("agent:A consumer mcp_server:R", "allowed_run_source", **CHAT_ONLY)},
    ),
    # system tool/skill/MCP x allowed org -> allowed_org + organization#member consumer
    *[
        (
            f"system_{kind}_allowed_org",
            fanout(kind, "allowed_org"),
            specs(f"organization:O allowed_org {kind}:R"),
        )
        for kind in ("tool", "skill", "mcp_server")
    ],
    *[
        (f"system_{kind}_fanout", fanout(kind), specs(f"organization:O#member consumer {kind}:R"))
        for kind in ("tool", "skill", "mcp_server")
    ],
    # visible model x company x org -> organization#member consumer llm_model
    (
        "visible_model_fanout",
        fanout("llm_model", resource_id=M),
        specs("organization:O#member consumer llm_model:M"),
    ),
    # company credential (scope=tenant) x allowed org -> allowed_org + consumer
    (
        "tenant_credential_allowed_org",
        grant("credential", "allowed_org", "organization", ORG),
        specs("organization:O allowed_org credential:R"),
    ),
    (
        "tenant_credential_consumer",
        grant("credential", "consumer", "organization", ORG),
        specs("organization:O#member consumer credential:R"),
    ),
    # system marketplace item x org -> organization#member viewer (C-01 backfill: installer)
    (
        "system_marketplace_viewer",
        fanout("marketplace_item", "viewer", tenant_id=ID["T"]),
        specs(
            "organization:O#member viewer marketplace_item:R",
            "tenant:T tenant marketplace_item:R",
        ),
    ),
    (
        "system_marketplace_installer_migration",
        grant(
            "marketplace_item",
            "installer",
            "organization",
            ORG,
            grant_kind="migration",
            tenant_id=ID["T"],
        ),  # fmt: skip
        specs(
            "organization:O#member installer marketplace_item:R",
            "tenant:T tenant marketplace_item:R",
        ),
    ),
]


@pytest.mark.parametrize(("row", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_grant_maps_to_exact_tuple_set(row: rows.Row, expected: set[RelationTuple]):
    produced = tuples_for(row, now=NOW)
    assert set(produced) == expected
    assert len(produced) == len(expected)
    assert [model_violation(item) for item in produced] == [None] * len(produced)


def test_condition_and_expiry_together_are_rejected():
    row = grant(
        "agent", "consumer", "user", U, expires_at=NOW + timedelta(days=1),
        condition_name="allowed_run_source", condition_context={"allowed_sources": []},
    )  # fmt: skip
    with pytest.raises(TupleRuleError, match="expires_at"):
        tuples_for(row, now=NOW)


def test_unknown_principal_type_is_rejected():
    with pytest.raises(TupleRuleError, match="principal"):
        tuples_for(grant("skill", "viewer", "tenant", U), now=NOW)


def test_non_positive_lifetime_yields_nothing():
    row = grant("agent", "consumer", "user", U, granted_at=NOW, expires_at=NOW)
    assert tuples_for(row, now=NOW - timedelta(days=1)) == ()


def test_naive_timestamps_are_utc_and_sub_second_durations_keep_precision():
    row = grant(
        "agent", "consumer", "user", U, granted_at=GRANTED.replace(tzinfo=None),
        expires_at=(GRANTED + timedelta(seconds=1.5)).replace(tzinfo=None),
    )  # fmt: skip
    (item,) = tuples_for(row, now=GRANTED)
    assert item.condition is not None
    assert item.condition.context == {
        "grant_time": "2026-10-01T00:00:00Z",
        "grant_duration": "1500000us",
    }


def test_condition_context_encoding_is_canonical():
    first = RelationTuple.of("user:u", "explorer", "db_connection:d", "c", {"b": 1, "a": [2]})
    second = RelationTuple.of("user:u", "explorer", "db_connection:d", "c", {"a": [2], "b": 1})
    assert first == second
    assert first.condition is not None
    assert first.condition.context_json == '{"a":[2],"b":1}'


def test_mcp_delegation_without_stored_sources_is_rejected():
    row = grant("mcp_server", "consumer", "agent", A, grant_kind="delegation")
    with pytest.raises(TupleRuleError, match="allowed_run_source"):
        tuples_for(row, now=NOW)


@pytest.mark.parametrize("resource_type", ["skill", "tool", "mcp_server", "credential"])
def test_expiring_grant_outside_agent_consumer_is_rejected_by_the_model(resource_type: str):
    row = grant(resource_type, "consumer", "user", U, expires_at=IN_30_DAYS)
    with pytest.raises(TupleRuleError, match="non_expired_grant"):
        tuples_for(row, now=NOW)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (grant("agent", "owner", "group", G), "not assignable"),
        (grant("tool", "installer", "user", U), "no directly assignable relation"),
        (grant("skill", "viewer", "user", U, condition_name="in_company_network",
               condition_context={"cidr": "10.0.0.0/8"}), "not assignable"),
        (grant("mcp_server", "consumer", "agent", A, condition_name="allowed_run_source",
               condition_context={"sources": ["CHAT"]}), "context"),
    ],
)  # fmt: skip
def test_model_guard_rejects_unassignable_tuples(row: rows.GrantRow, message: str):
    with pytest.raises(TupleRuleError, match=message):
        tuples_for(row, now=NOW)
