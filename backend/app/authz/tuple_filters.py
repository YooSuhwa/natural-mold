"""Cross-row admission for ``expected_tuples`` (plan 7.2 / 7.2.1 removal and archive semantics).

A row only projects while the principal it names is still live in the same scope:
roles, capabilities, group membership and direct grants of a user require an *active*
membership (an ``admin`` role would otherwise re-derive ``member`` for a suspended user),
group principals require the group, agent principals require an agent resident in the
grant's organization, ``share``/``delegation`` grants on or by an archived agent are dropped
(7.2.1 "에이전트 보관"; owner/org/resident tuples and other grant kinds stay), and
``llm_model`` fanout requires a visible model.
"""

from collections.abc import Iterator
from uuid import UUID

from app.authz import tuple_rows as rows

_ARCHIVE_REVOKED_KINDS = frozenset({"share", "delegation"})


class _Live:
    def __init__(self, snapshot: rows.Snapshot) -> None:
        self.tenant_members = {
            (m.tenant_id, m.user_id) for m in snapshot.tenant_members if m.status == "active"
        }
        self.org_members = {
            (m.org_id, m.user_id) for m in snapshot.org_members if m.status == "active"
        }
        self.groups = {(g.org_id, g.id) for g in snapshot.groups}
        agents = [r for r in snapshot.resources if r.resource_type == "agent"]
        self.agents = {(a.org_id, a.id) for a in agents}
        self.archived_agents = {a.id for a in agents if a.archived_at is not None}
        self.visible_models = {m.id for m in snapshot.models if m.is_visible}

    def principal(self, org: UUID, principal_type: str, principal_id: UUID) -> bool:
        match principal_type:
            case "user":
                return (org, principal_id) in self.org_members
            case "group":
                return (org, principal_id) in self.groups
            case "agent":
                return (org, principal_id) in self.agents
            case _:
                return True

    def grant(self, row: rows.GrantRow) -> bool:
        archived = (row.resource_type == "agent" and row.resource_id in self.archived_agents) or (
            row.principal_type == "agent" and row.principal_id in self.archived_agents
        )
        if archived and row.grant_kind in _ARCHIVE_REVOKED_KINDS:
            return False
        if row.resource_type == "llm_model" and row.resource_id not in self.visible_models:
            return False
        return self.principal(row.org_id, row.principal_type, row.principal_id)


def admitted_rows(snapshot: rows.Snapshot) -> Iterator[rows.Row]:
    live = _Live(snapshot)
    yield from snapshot.tenant_members
    yield from (r for r in snapshot.tenant_roles if (r.tenant_id, r.user_id) in live.tenant_members)
    yield from snapshot.organizations
    yield from snapshot.org_members
    yield from (
        r for r in snapshot.org_roles if live.principal(r.org_id, r.principal_type, r.principal_id)
    )
    yield from snapshot.groups
    yield from (
        m
        for m in snapshot.group_members
        if (m.org_id, m.group_id) in live.groups and (m.org_id, m.user_id) in live.org_members
    )
    yield from (
        c
        for c in snapshot.capabilities
        if live.principal(c.org_id, c.principal_type, c.principal_id)
    )
    yield from snapshot.resources
    yield from snapshot.credentials
    yield from snapshot.marketplace_items
    yield from snapshot.mcp_tools
    yield from (g for g in snapshot.grants if live.grant(g))
