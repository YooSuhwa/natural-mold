"""F-01 (plan m86_agent_links_delegation, 8.x, 10.6): link delegation columns and backfill.

* ``agent_tools``, ``agent_mcp_tools``, ``agent_skills``, ``agent_subagents``: ``required``
  (default false), ``delegated_by`` (FK users, SET NULL), ``link_status`` (active/broken),
  ``broken_reason``. ``agents.llm_credential_delegated_by`` (FK users, SET NULL).
* Backfill (10.6 "m86 이전"): every existing link was made by the agent owner (only the
  owner could attach), so ``delegated_by`` = ``agents.user_id`` and each delegated object
  gets one active ``delegation`` grant ``agent:A consumer R`` granted by the owner:
  tools, skills, subagents and the model credential per link, MCP servers once per server
  (10.6: MCP tools delegate through their server) with ``allowed_run_source`` over every
  run source (7.1 default). ``org_id``/``tenant_id`` are the agent's (``GrantRow``).
* ``source_link`` = ``{"link": <link table>, "migration": "m86"}``; F-02 writes the same
  ``link`` key, so later link changes revoke backfilled grants like their own.

* Objects of another organization/company and archived subagents get ``delegated_by``
  but no grant (the F-02 ``_grantable`` rule, D21); F-03 reports those links.

Set-based: one scope read per object type, one read of active keys (skipped, so reruns
add nothing), one batched insert with deterministic ids. OpenFGA tuples are not written
here (``authz bootstrap`` projects them after deploy). Downgrade deletes the agent
delegations and their queued outbox rows; tuples already in OpenFGA need ``authz rebuild``.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa

from alembic import op

revision = "f01_connection_delegation"
down_revision = "f04_credential_modes"
branch_labels = None
depends_on = None

LINK_TABLES = ("agent_tools", "agent_mcp_tools", "agent_skills", "agent_subagents")
RUN_SOURCES = ["CHAT", "TRIGGER", "CHANNEL", "SUBAGENT", "API"]  # tuple_types.RUN_SOURCES

_grants = sa.table(
    "resource_grants",
    *(
        sa.column(name, sa.Uuid())
        for name in ("id", "org_id", "tenant_id", "resource_id", "principal_id", "granted_by")
    ),
    *(
        sa.column(name, sa.String())
        for name in (
            "state",
            "resource_type",
            "relation",
            "principal_type",
            "grant_kind",
            "condition_name",
        )
    ),
    sa.column("condition_context", sa.JSON()),
    sa.column("source_link", sa.JSON()),
)

# (resource_type, link table, SELECT agent_id, resource_id)
_DELEGATED = (
    ("tool", "agent_tools", "SELECT agent_id, tool_id FROM agent_tools"),
    (
        "mcp_server",
        "agent_mcp_tools",
        "SELECT DISTINCT l.agent_id, t.server_id FROM agent_mcp_tools AS l "
        "JOIN mcp_tools AS t ON t.id = l.mcp_tool_id",
    ),
    ("skill", "agent_skills", "SELECT agent_id, skill_id FROM agent_skills"),
    ("agent", "agent_subagents", "SELECT parent_agent_id, sub_agent_id FROM agent_subagents"),
    (
        "credential",
        "agents.llm_credential_id",
        "SELECT id, llm_credential_id FROM agents WHERE llm_credential_id IS NOT NULL",
    ),
)


def _add_columns() -> None:
    for table in LINK_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("required", sa.Boolean(), server_default=sa.false(), nullable=False)
            )
            batch.add_column(sa.Column("delegated_by", sa.Uuid(), nullable=True))
            batch.add_column(
                sa.Column("link_status", sa.String(20), server_default="active", nullable=False)
            )
            batch.add_column(sa.Column("broken_reason", sa.String(64), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_delegated_by", "users", ["delegated_by"], ["id"], ondelete="SET NULL"
            )
            batch.create_check_constraint(
                f"ck_{table}_link_status", "link_status IN ('active','broken')"
            )
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("llm_credential_delegated_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_agents_llm_credential_delegated_by",
            "users",
            ["llm_credential_delegated_by"],
            ["id"],
            ondelete="SET NULL",
        )


def _backfill_delegators() -> None:
    owner = "(SELECT a.user_id FROM agents AS a WHERE a.id = {table}.{column})"
    for table in LINK_TABLES:
        column = "parent_agent_id" if table == "agent_subagents" else "agent_id"
        op.execute(
            sa.text(f"UPDATE {table} SET delegated_by = {owner.format(table=table, column=column)}")
        )
    op.execute(
        sa.text(
            "UPDATE agents SET llm_credential_delegated_by = user_id "
            "WHERE llm_credential_id IS NOT NULL"
        )
    )


_SCOPES = {  # resource_type -> (table, archived column or None)
    "tool": ("tools", None),
    "mcp_server": ("mcp_servers", None),
    "skill": ("skills", None),
    "agent": ("agents", "archived_at"),
    "credential": ("credentials", None),
}
Scope = tuple[UUID | None, UUID | None, bool]  # org_id, tenant_id, archived


def _scopes(connection: sa.Connection, resource_type: str) -> dict[UUID, Scope]:
    table, archived = _SCOPES[resource_type]
    flag = f"{archived} IS NOT NULL" if archived else "0 = 1"
    rows = connection.execute(sa.text(f"SELECT id, org_id, tenant_id, {flag} FROM {table}"))  # noqa: S608
    return {_uuid(rid): (_opt(org), _opt(tenant), bool(gone)) for rid, org, tenant, gone in rows}


def _opt(value: object) -> UUID | None:
    return None if value is None else _uuid(value)


def _grantable(scope: Scope | None, agent: sa.Row) -> bool:
    """F-02 ``_grantable``: same organization/company (or platform rows), not archived."""
    if scope is None:
        return False
    org, tenant, archived = scope
    return (
        not archived
        and tenant in (None, _uuid(agent.tenant_id))
        and org in (None, _uuid(agent.org_id))
    )


def _held(connection: sa.Connection) -> set[tuple[str, UUID, UUID]]:
    rows = connection.execute(
        sa.select(_grants.c.resource_type, _grants.c.resource_id, _grants.c.principal_id).where(
            _grants.c.state == "active",
            _grants.c.relation == "consumer",
            _grants.c.principal_type == "agent",
        )
    )
    return {(kind, _uuid(rid), _uuid(agent)) for kind, rid, agent in rows}


def _backfill_grants(connection: sa.Connection) -> None:
    """Set-based: one scope read per type, one read of active keys, one batched insert.

    Objects of another organization/company or archived subagents keep the link (and its
    ``delegated_by``) but get no grant, as F-02 decides (D21); F-03 reports them."""
    agents = {
        _uuid(row.id): row
        for row in connection.execute(sa.text("SELECT id, user_id, org_id, tenant_id FROM agents"))
    }
    held = _held(connection)
    values: list[dict[str, object]] = []
    for resource_type, link, query in _DELEGATED:
        scopes = _scopes(connection, resource_type)
        for agent_id, resource_id in connection.execute(sa.text(query)).all():
            agent, rid = agents[_uuid(agent_id)], _uuid(resource_id)
            key = (resource_type, rid, _uuid(agent.id))
            if key in held or not _grantable(scopes.get(rid), agent):
                continue
            held.add(key)
            values.append(_row(agent, resource_type, rid, link))
    if values:
        connection.execute(sa.insert(_grants), values)


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _row(agent: sa.Row, resource_type: str, resource_id: UUID, link: str) -> dict[str, object]:
    agent_id = _uuid(agent.id)
    mcp = resource_type == "mcp_server"
    return {
        "id": uuid5(NAMESPACE_URL, f"moldy/m86/{agent_id}/{resource_type}/{resource_id}"),
        "org_id": _uuid(agent.org_id),
        "tenant_id": _uuid(agent.tenant_id),
        "state": "active",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "relation": "consumer",
        "principal_type": "agent",
        "principal_id": agent_id,
        "grant_kind": "delegation",
        "granted_by": _uuid(agent.user_id),
        "condition_name": "allowed_run_source" if mcp else None,
        "condition_context": {"allowed_sources": RUN_SOURCES} if mcp else None,
        "source_link": {"link": link, "migration": "m86"},
    }


def upgrade() -> None:
    _add_columns()
    _backfill_delegators()
    _backfill_grants(op.get_bind())


def downgrade() -> None:
    # Agent delegations exist only from this revision on (m86 backfill and F-02 writes).
    # Their queued outbox rows go too; tuples already written to OpenFGA stay until an
    # operator runs ``authz rebuild`` (the store is a derived index, D2).
    delegations = (
        "SELECT id FROM resource_grants "
        "WHERE grant_kind = 'delegation' AND principal_type = 'agent'"
    )
    op.execute(
        sa.text(
            "DELETE FROM authz_outbox WHERE source_table = 'resource_grants' "
            f"AND source_id IN ({delegations})"  # noqa: S608
        )
    )
    op.execute(sa.text(f"DELETE FROM resource_grants WHERE id IN ({delegations})"))  # noqa: S608
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint("fk_agents_llm_credential_delegated_by", type_="foreignkey")
        batch.drop_column("llm_credential_delegated_by")
    for table in LINK_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"ck_{table}_link_status", type_="check")
            batch.drop_constraint(f"fk_{table}_delegated_by", type_="foreignkey")
            for column in ("broken_reason", "link_status", "delegated_by", "required"):
                batch.drop_column(column)
