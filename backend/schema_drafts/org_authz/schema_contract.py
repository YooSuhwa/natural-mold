"""Canonical reflected field inventory for the reviewed m89 schema contract."""

from pathlib import Path
from typing import Final, assert_never

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DefaultClause, FetchedValue, MetaData

from schema_drafts.org_authz.schema import NEW_TABLES
from schema_drafts.org_authz.scope_columns import M81_SCOPED, M89_SCOPED, TENANT_ONLY


class ColumnContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    type: str
    nullable: bool
    default: str | None
    primary_key: bool


class ForeignKeyContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str | None
    columns: tuple[str, ...]
    targets: tuple[str, ...]
    ondelete: str | None


class IndexContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str | None
    columns: tuple[str, ...]
    unique: bool
    predicate: str | None


class CheckContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str | None
    expression: str


class UniqueContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str | None
    columns: tuple[str, ...]


class TableContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    columns: tuple[ColumnContract, ...]
    foreign_keys: tuple[ForeignKeyContract, ...]
    indexes: tuple[IndexContract, ...]
    checks: tuple[CheckContract, ...]
    unique_constraints: tuple[UniqueContract, ...]
    primary_key_name: str | None


class SchemaContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    stage: str
    source_6_1: tuple[tuple[str, ...], ...]
    source_6_1_codes: tuple[str, ...]
    source_6_2: tuple[tuple[str, ...], ...]
    source_6_3: tuple[tuple[str, ...], ...]
    new_tables: tuple[str, ...]
    tables: tuple[TableContract, ...]


class UnrepresentableDefaultError(ValueError):
    """A generated server default cannot be represented as a SQL literal."""

    def __init__(self, column: str) -> None:
        super().__init__(f"Cannot inventory generated default for {column}")


CONTRACT_PATH: Final = Path(__file__).with_name("schema_contract.json")
REVIEWED_TABLES: Final = frozenset(
    (
        *NEW_TABLES,
        *M81_SCOPED,
        *M89_SCOPED,
        *TENANT_ONLY,
        "users",
        "agent_tools",
        "agent_mcp_tools",
        "agent_skills",
        "agent_subagents",
        "agent_trigger_runs",
    )
)


def table_inventory(metadata: MetaData) -> tuple[TableContract, ...]:
    """Canonicalize real reflected SQL types, nulls, defaults, FK actions and indexes."""
    from sqlalchemy import CheckConstraint, UniqueConstraint

    tables: list[TableContract] = []
    for name in sorted(REVIEWED_TABLES):
        table = metadata.tables[name]
        columns: list[ColumnContract] = []
        for column in sorted(table.c, key=lambda column: column.name):
            match column.server_default:
                case None:
                    default = None
                case DefaultClause(arg=argument):
                    default = str(argument)
                case FetchedValue():
                    raise UnrepresentableDefaultError(f"{name}.{column.name}")
                case unreachable:
                    assert_never(unreachable)
            columns.append(
                ColumnContract(
                    name=column.name,
                    type=str(column.type),
                    nullable=bool(column.nullable),
                    default=default,
                    primary_key=column.primary_key,
                )
            )
        fks = tuple(
            sorted(
                (
                    ForeignKeyContract(
                        name=str(fk.name) if fk.name is not None else None,
                        columns=tuple(fk.column_keys),
                        targets=tuple(element.target_fullname for element in fk.elements),
                        ondelete=fk.ondelete,
                    )
                    for fk in table.foreign_key_constraints
                ),
                key=lambda fk: (fk.columns, fk.targets),
            )
        )
        indexes = tuple(
            IndexContract(
                name=index.name,
                columns=tuple(column.name for column in index.columns),
                unique=index.unique,
                predicate=str(index.dialect_options["sqlite"]["where"])
                if index.dialect_options["sqlite"].get("where") is not None
                else None,
            )
            for index in sorted(table.indexes, key=lambda index: index.name or "")
        )
        checks = tuple(
            sorted(
                (
                    CheckContract(
                        name=str(constraint.name) if constraint.name is not None else None,
                        expression=str(constraint.sqltext),
                    )
                    for constraint in table.constraints
                    if isinstance(constraint, CheckConstraint)
                ),
                key=lambda constraint: (constraint.expression, constraint.name or ""),
            )
        )
        uniques = tuple(
            sorted(
                (
                    UniqueContract(
                        name=str(constraint.name) if constraint.name is not None else None,
                        columns=tuple(column.name for column in constraint.columns),
                    )
                    for constraint in table.constraints
                    if isinstance(constraint, UniqueConstraint)
                ),
                key=lambda constraint: (constraint.columns, constraint.name or ""),
            )
        )
        tables.append(
            TableContract(
                name=name,
                columns=tuple(columns),
                foreign_keys=fks,
                indexes=indexes,
                checks=checks,
                unique_constraints=uniques,
                primary_key_name=str(table.primary_key.name)
                if table.primary_key.name is not None
                else None,
            )
        )
    return tuple(tables)


def schema_contract() -> SchemaContract:
    """Parse the checked-in m89 review baseline, independent of current DDL."""
    return SchemaContract.model_validate_json(CONTRACT_PATH.read_text())
