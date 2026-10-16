"""Plan 7.2: Postgres rows -> OpenFGA tuples. Pure; outbox, bootstrap and reconcile share it.

Outbox note (C-04): ``expected_tuples`` merges rows that project the same tuple key, so a
delete for one source row must recompute the key from the *remaining* rows (``tuples_for``
over them, merged) and rewrite instead of deleting while another row still projects it.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.authz import tuple_rows as rows
from app.authz.tuple_filters import admitted_rows
from app.authz.tuple_merge import merge_tuples
from app.authz.tuple_model_guard import assert_model_valid
from app.authz.tuple_types import (
    EXPIRY_CONDITION,
    RUN_SOURCE_CONDITION,
    RUN_SOURCES,
    Condition,
    RelationTuple,
    TupleRuleError,
)

__all__ = [
    "RUN_SOURCES",
    "Condition",
    "RelationTuple",
    "TupleRuleError",
    "expected_tuples",
    "tuples_for",
]


def _ref(kind: str, value: UUID) -> str:
    return f"{kind}:{value}"


def _principal(principal_type: str, principal_id: UUID, relation: str) -> str:
    match principal_type:
        case "user" | "agent":
            return _ref(principal_type, principal_id)
        case "organization" if relation == "allowed_org":
            return _ref("organization", principal_id)
        case "group" | "organization":
            return f"{_ref(principal_type, principal_id)}#member"
        case _:
            raise TupleRuleError(f"unknown principal type {principal_type!r}")


def _org_and_owner(kind: str, rid: UUID, org: UUID | None, owner: UUID | None, system: bool):
    if system:
        return ()
    obj = _ref(kind, rid)
    found = [] if org is None else [RelationTuple(_ref("organization", org), "org", obj)]
    if owner is not None:
        found.append(RelationTuple(_ref("user", owner), "owner", obj))
    return tuple(found)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _duration(value: timedelta) -> str:
    micros = value // timedelta(microseconds=1)
    return f"{micros // 1_000_000}s" if micros % 1_000_000 == 0 else f"{micros}us"


def _grant_tuples(row: rows.GrantRow, now: datetime) -> tuple[RelationTuple, ...]:
    if row.state != "active":
        return ()
    found = _marketplace_tenant(row)
    return (*found, *_principal_tuple(row, now))


def _marketplace_tenant(row: rows.GrantRow) -> tuple[RelationTuple, ...]:
    """An org fanout on a marketplace item also records that org's tenant on the item.

    ``can_view = viewer and member from tenant`` needs a tenant tuple; system items have no
    tenant of their own, so each fanout org's tenant is attached (several orgs of one tenant
    project the same tuple, merged by ``expected_tuples``). 7.2 lists the system fanout as
    ``viewer``; the C-01 backfill writes ``installer`` (legacy system items were installable
    by every org) and both relations are projected as stored.
    """
    if row.resource_type != "marketplace_item" or row.principal_type != "organization":
        return ()
    if row.tenant_id is None:
        return ()
    item = _ref("marketplace_item", row.resource_id)
    return (RelationTuple(_ref("tenant", row.tenant_id), "tenant", item),)


def _principal_tuple(row: rows.GrantRow, now: datetime) -> tuple[RelationTuple, ...]:
    user = _principal(row.principal_type, row.principal_id, row.relation)
    obj = _ref(row.resource_type, row.resource_id)
    if row.expires_at is not None:
        if row.condition_name is not None:
            raise TupleRuleError(f"grant {row.id}: expires_at cannot combine with a condition")
        start, end = _utc(row.granted_at), _utc(row.expires_at)
        if end <= _utc(now) or end <= start:
            return ()
        context = {
            "grant_time": start.isoformat().replace("+00:00", "Z"),
            "grant_duration": _duration(end - start),
        }
        return (RelationTuple.of(user, row.relation, obj, EXPIRY_CONDITION, context),)
    if row.condition_name is not None:
        context = row.condition_context or {}
        return (RelationTuple.of(user, row.relation, obj, row.condition_name, context),)
    if row.resource_type == "mcp_server" and row.principal_type == "agent":
        # Plan 7.1: agent delegation to an MCP server always carries the run-source condition.
        # Least privilege: the writer must store the chosen sources; no implicit "all".
        raise TupleRuleError(f"grant {row.id}: MCP server delegation needs {RUN_SOURCE_CONDITION}")
    return (RelationTuple(user, row.relation, obj),)


def tuples_for(row: rows.Row, *, now: datetime) -> tuple[RelationTuple, ...]:
    """Tuples one source row projects on its own (cross-row filters: ``expected_tuples``).

    Every tuple is checked against ``model.json``; a row the model would reject raises
    ``TupleRuleError`` instead of reaching OpenFGA.
    """
    produced = _row_tuples(row, now)
    for item in produced:
        assert_model_valid(item)
    return produced


def _row_tuples(row: rows.Row, now: datetime) -> tuple[RelationTuple, ...]:
    match row:
        case rows.TenantMemberRow(tenant_id=t, user_id=u, status="active"):
            return (RelationTuple(_ref("user", u), "member", _ref("tenant", t)),)
        case rows.TenantRoleRow(tenant_id=t, user_id=u, role=role):
            return (RelationTuple(_ref("user", u), role, _ref("tenant", t)),)
        case rows.OrganizationRow(id=o, tenant_id=t):
            # Archived/suspended organizations keep tuples; requests are refused upstream (7.2.1).
            return (RelationTuple(_ref("tenant", t), "tenant", _ref("organization", o)),)
        case rows.OrgMemberRow(org_id=o, user_id=u, status="active"):
            return (RelationTuple(_ref("user", u), "member", _ref("organization", o)),)
        case rows.OrgRoleRow(org_id=o, role=role, principal_type=kind, principal_id=p):
            return (RelationTuple(_principal(kind, p, role), role, _ref("organization", o)),)
        case rows.GroupRow(id=g, org_id=o):
            return (RelationTuple(_ref("organization", o), "org", _ref("group", g)),)
        case rows.GroupMemberRow(group_id=g, user_id=u):
            return (RelationTuple(_ref("user", u), "member", _ref("group", g)),)
        case rows.CapabilityRow(org_id=o, capability=cap, principal_type=kind, principal_id=p):
            return (RelationTuple(_principal(kind, p, cap), cap, _ref("organization", o)),)
        case rows.ResourceRow(resource_type=kind, id=rid, org_id=o, owner_id=owner):
            found = _org_and_owner(kind, rid, o, owner, row.is_system)
            if kind == "agent" and o is not None:
                resident = RelationTuple(
                    _ref("agent", rid), "resident_agent", _ref("organization", o)
                )
                found = (*found, resident)
            return found
        case rows.CredentialRow(id=rid, org_id=o, tenant_id=t, owner_id=owner):
            found = _org_and_owner("credential", rid, o, owner, row.is_system)
            if row.scope == "tenant" and t is not None:
                found = (
                    RelationTuple(_ref("tenant", t), "tenant", _ref("credential", rid)),
                    *found,
                )
            return found
        case rows.MarketplaceItemRow(id=rid, org_id=o, tenant_id=t, owner_id=owner):
            if row.is_system or t is None:
                return _org_and_owner("marketplace_item", rid, o, owner, row.is_system)
            obj = _ref("marketplace_item", rid)
            found = (
                *_org_and_owner("marketplace_item", rid, o, owner, False),
                RelationTuple(_ref("tenant", t), "tenant", obj),
            )
            if row.tenant_publication == "approved":
                found = (*found, RelationTuple(f"{_ref('tenant', t)}#member", "installer", obj))
            return found
        case rows.McpToolRow(id=rid, server_id=server):
            return (RelationTuple(_ref("mcp_server", server), "server", _ref("mcp_tool", rid)),)
        case rows.GrantRow():
            return _grant_tuples(row, now)
        case _:
            return ()


def expected_tuples(snapshot: rows.Snapshot, *, now: datetime) -> tuple[RelationTuple, ...]:
    """The exact OpenFGA store contents for ``snapshot``: one tuple per key, sorted."""
    return merge_tuples(
        item for row in admitted_rows(snapshot) for item in tuples_for(row, now=now)
    )
