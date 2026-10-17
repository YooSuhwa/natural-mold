"""F-01 backfill rules: the D21 filter, rerun safety and downgrade (plan m86, 10.6)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, text

from tests.authz.test_f01_connection_delegation import (
    NEW,
    Lane,
    T,
    _columns,
    _delegations,
    _seed_graph,
)
from tests.authz.test_f01_connection_delegation import lane as lane  # noqa: F401 (fixture)


def test_cross_org_objects_and_archived_subagents_get_a_delegator_but_no_grant(
    lane: Lane,
) -> None:
    parent = lane.agent()
    other_org = uuid4()
    lane.connection.execute(
        insert(T["organizations"]).values(id=other_org, tenant_id=lane.tenant, name="P", slug="p")
    )
    foreign = lane.add(
        "tools",
        definition_key="http_request",
        name="f",
        user_id=lane.owner,
        org_id=other_org,
        tenant_id=lane.tenant,
    )
    archived = lane.agent(archived_at=datetime(2026, 1, 1))
    lane.add("agent_tools", agent_id=parent, tool_id=foreign)
    lane.add(
        "agent_subagents",
        parent_agent_id=parent,
        sub_agent_id=archived,
        position=0,
        created_at=datetime(2026, 1, 1),
    )

    lane.migration.upgrade()

    assert _delegations(lane.connection) == []  # D21: the F-02 ``_grantable`` rule
    for table in ("agent_tools", "agent_subagents"):
        owner = lane.connection.scalar(text(f"SELECT delegated_by FROM {table}"))  # noqa: S608
        assert UUID(str(owner)) == lane.owner


def test_rerun_adds_nothing_and_downgrade_removes_delegations(lane: Lane) -> None:
    _seed_graph(lane)
    lane.migration.upgrade()
    lane.migration._backfill_grants(lane.connection)  # a rerun of the data step
    grants = _delegations(lane.connection)
    assert len(grants) == 6
    grant_id = lane.connection.scalar(text("SELECT id FROM resource_grants LIMIT 1"))
    lane.connection.execute(
        text(
            "INSERT INTO authz_outbox (id, seq, tuple_key, op, tuple, source_table, source_id, "
            "attempts, next_attempt_at, created_at) VALUES (:id, 1, 'k', 'write', '{}', "
            "'resource_grants', :src, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": uuid4().hex, "src": grant_id},
    )

    lane.migration.downgrade()

    assert _delegations(lane.connection) == []
    assert lane.connection.scalar(text("SELECT count(*) FROM authz_outbox")) == 0
    assert not NEW & _columns(lane.connection, "agent_subagents")
