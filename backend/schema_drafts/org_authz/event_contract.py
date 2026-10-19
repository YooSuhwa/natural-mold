"""Review-only typed 7.2.1 source crosswalk; no event service or FGA execution."""

from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import MetaData

RevocationScope = Literal[
    "grant", "membership", "group_member", "role", "capability", "ownership", "resource"
]
OrderingStep = Literal[
    "source_and_outbox_commit",
    "fga_write",
    "marker_and_source_and_outbox_commit",
    "fga_delete",
    "finalize_marker",
    "request_status_gate",
    "tenant_deletion_gate",
]


class ColumnSource(BaseModel):
    """Concrete columns required by a mutation or tuple projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    table: str
    columns: tuple[str, ...] = Field(min_length=1)
    mutation: str


class TupleProjection(BaseModel):
    """Symbolic tuple formula and its persisted source; not an FGA SDK payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    template: str
    sources: tuple[ColumnSource, ...] = Field(min_length=1)


class EventContract(BaseModel):
    """One exact source row plus its explicitly resolved draft interpretation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    source_cells: tuple[str, str, str, str, str]
    db: tuple[ColumnSource, ...] = Field(min_length=1)
    writes: tuple[TupleProjection, ...]
    deletes: tuple[TupleProjection, ...]
    scopes: tuple[RevocationScope, ...]
    marker_target: str
    write_order: tuple[OrderingStep, ...]
    delete_order: tuple[OrderingStep, ...]
    resolution: str | None = None


CONTRACT_PATH: Final = Path(__file__).with_suffix(".json")
WRITE_ORDER: Final = ("source_and_outbox_commit", "fga_write")
DELETE_ORDER: Final = ("marker_and_source_and_outbox_commit", "fga_delete", "finalize_marker")
INFRASTRUCTURE: Final = (
    ColumnSource(
        table="authz_outbox",
        columns=("op", "tuple", "source_table", "source_id", "created_at", "processed_at"),
        mutation="transactional projection",
    ),
    ColumnSource(
        table="authz_revocations",
        columns=(
            "org_id",
            "tenant_id",
            "scope",
            "subject",
            "object",
            "fga_deleted_at",
            "finalized_at",
        ),
        mutation="revocation lifecycle",
    ),
    ColumnSource(
        table="organizations", columns=("authz_epoch",), mutation="increment on revocation"
    ),
)


def event_contracts() -> tuple[EventContract, ...]:
    """Parse checked-in JSON once at the review boundary."""
    return TypeAdapter(tuple[EventContract, ...]).validate_json(CONTRACT_PATH.read_text())


def missing_sources(metadata: MetaData) -> tuple[str, ...]:
    """Report absent tables/columns against actual migrated, reflected metadata."""
    sources = list(INFRASTRUCTURE)
    for event in event_contracts():
        sources.extend(event.db)
        for projection in (*event.writes, *event.deletes):
            sources.extend(projection.sources)
    missing: set[str] = set()
    for source in sources:
        table = metadata.tables.get(source.table)
        if table is None:
            missing.add(source.table)
            continue
        missing.update(
            f"{source.table}.{column}" for column in source.columns if column not in table.c
        )
    return tuple(sorted(missing))
