"""Which rows of every table belong to one company (I-07 export and permanent purge).

Derived from the ORM metadata, so a new table is covered without a list to maintain:

* a table with ``tenant_id`` holds the company's rows where ``tenant_id = :company``
  (other companies' rows never match); a row with a NULL ``tenant_id`` belongs to the
  company only through an *owning* key (``ON DELETE CASCADE`` or NOT NULL) into a
  company row, e.g. a ``token_usages`` row written without attribution;
* a table without it belongs to the company through any foreign key into a company
  table (``fk IN (SELECT parent key WHERE parent predicate)``): message events through
  conversations, MCP tools through servers, trigger runs through triggers, ...;
* ``tenants`` is the root (the purge deletes it last) and ``users`` are global accounts
  (never deleted; the export carries the company's members only).

``company_tables`` is parents-first (export order); its reverse is the FK-safe delete
order. ``inbound_references`` lists every reference from a row outside the company
(``users``, platform rows, *other companies' rows*) to a company row: the purge detaches
the nullable ones and refuses to start while a non-nullable one exists. Intra-company FK
cycles (``marketplace_items.latest_version_id``) are broken by ``cycle_breaks``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import Column, ColumnElement, MetaData, Table, and_, not_, or_, select, true
from sqlalchemy.schema import sort_tables_and_constraints

ROOT: Final = "tenants"
# ``tenant_purges`` is the purge receipt that outlives the company (no FK).
GLOBAL: Final = frozenset({ROOT, "users", "tenant_purges"})
REDACTED: Final = "<redacted>"
# Secret material never leaves the instance in an export (credentials, envelope keys,
# token digests, MCP env/header values); every ``*_encrypted`` column is added too.
SECRET_COLUMNS: Final[Mapping[str, frozenset[str]]] = {
    "credentials": frozenset({"data_encrypted"}),
    "mcp_servers": frozenset({"env_vars", "headers"}),
    "agent_api_keys": frozenset({"key_hash"}),
    "org_invitations": frozenset({"token_hash"}),
    "tenant_invitations": frozenset({"token_hash"}),
    "credential_oauth_states": frozenset({"state_hash"}),
    "share_links": frozenset({"share_token"}),
    "tenant_domains": frozenset({"txt_token"}),
    "tenants": frozenset({"data_keys"}),
    "users": frozenset(
        {
            "hashed_password",
            "email_verify_token",
            "password_reset_token",
            "password_reset_expires_at",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class CompanyTable:
    table: Table
    where: ColumnElement[bool]


@dataclass(frozen=True, slots=True)
class Inbound:
    """Rows outside the company whose ``column`` points at a company row."""

    table: Table
    column: Column[Any]
    where: ColumnElement[bool]
    nullable: bool


@dataclass(frozen=True, slots=True)
class Detach:
    """``UPDATE table SET column = NULL WHERE where`` before the purge deletes."""

    table: Table
    column: Column[Any]
    where: ColumnElement[bool]


def metadata() -> MetaData:
    import app.models  # noqa: F401 - registers every mapped table
    from app.database import Base

    return Base.metadata


def _link(
    column: Column[Any], parent: Column[Any], where: ColumnElement[bool]
) -> ColumnElement[bool]:
    return and_(column.is_not(None), column.in_(select(parent).where(where)))


def _links(
    table: Table, known: Mapping[str, ColumnElement[bool]], *, owning: bool = False
) -> list[ColumnElement[bool]]:
    return [
        _link(fk.parent, fk.column, known[fk.column.table.name])
        for fk in sorted(table.foreign_keys, key=lambda fk: fk.parent.name)
        if fk.column.table is not table
        and fk.column.table.name in known
        and (not owning or fk.ondelete == "CASCADE" or not fk.parent.nullable)
    ]


def _predicate(
    table: Table, tenant_id: UUID, known: Mapping[str, ColumnElement[bool]]
) -> ColumnElement[bool] | None:
    if table.name in GLOBAL:
        return None
    if "tenant_id" not in table.c:
        links = _links(table, known)
        return or_(*links) if links else None
    own = table.c.tenant_id == tenant_id
    owning = _links(table, known, owning=True) if table.c.tenant_id.nullable else []
    return or_(own, and_(table.c.tenant_id.is_(None), or_(*owning))) if owning else own


def company_tables(tenant_id: UUID) -> list[CompanyTable]:
    """Every company-scoped table with its row predicate, parents first."""
    known: dict[str, ColumnElement[bool]] = {}
    found: list[CompanyTable] = []
    for table in metadata().sorted_tables:
        where = _predicate(table, tenant_id, known)
        if where is not None:
            known[table.name] = where
            found.append(CompanyTable(table, where))
    return found


def _outside_rows(
    table: Table, tenant_id: UUID, known: Mapping[str, ColumnElement[bool]]
) -> ColumnElement[bool] | None:
    """Rows of ``table`` that are not the company's (``None``: the table has none)."""
    if table.name not in known:
        return true()  # users and other company-less tables
    if "tenant_id" not in table.c:
        return None  # reached through a company parent: deleted with the company
    column = table.c.tenant_id
    others = and_(column.is_not(None), column != tenant_id)
    owning = _links(table, known, owning=True)
    platform = and_(column.is_(None), not_(or_(*owning))) if owning else column.is_(None)
    return or_(others, platform)


def inbound_references(tenant_id: UUID, tables: list[CompanyTable]) -> list[Inbound]:
    """Every reference from a row outside the company (users, platform rows, other
    companies) to a company row, one per (table, column, parent)."""
    known = {item.table.name: item.where for item in tables}
    found: list[Inbound] = []
    for table in metadata().sorted_tables:
        if table.name == ROOT:
            continue
        outside = _outside_rows(table, tenant_id, known)
        if outside is None:
            continue
        for column in table.c:
            if column.name == "tenant_id":
                continue
            for fk in column.foreign_keys:
                parent = fk.column.table
                if parent is table or parent.name not in known:
                    continue
                target = _link(column, fk.column, known[parent.name])
                found.append(Inbound(table, column, and_(target, outside), bool(column.nullable)))
    return found


def cycle_breaks(tables: list[CompanyTable]) -> list[Detach]:
    """Nullable FK columns that close a cycle between company tables (cleared first)."""
    known = {item.table.name: item.where for item in tables}
    found: list[Detach] = []
    for table, constraints in sort_tables_and_constraints(metadata().sorted_tables):
        if table is not None:
            continue
        for constraint in constraints:
            owner = constraint.table
            if owner.name not in known:
                continue
            for column in constraint.columns:
                if column.nullable and column.name != "tenant_id":
                    found.append(Detach(owner, column, known[owner.name]))
    return found


def secret_columns(table: Table) -> frozenset[str]:
    named = SECRET_COLUMNS.get(table.name, frozenset())
    return named | {column.name for column in table.c if column.name.endswith("_encrypted")}
