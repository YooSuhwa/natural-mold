"""User-confirmed, reproducible checks for an immutable conversation artifact version."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ColumnName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ReferenceValue = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)
]
LogicalPath = Annotated[str, StringConstraints(min_length=1, max_length=500)]


def normalized_cell(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


class _FileCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_path: LogicalPath

    @field_validator("logical_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if (
            any(part in {"", ".", ".."} for part in value.split("/"))
            or "\\" in value
            or any(ord(ch) < 32 for ch in value)
        ):
            raise ValueError("Expected a relative artifact path")
        return value


class NonemptyFileCriterion(_FileCriterion):
    kind: Literal["nonempty_file"]


class CsvTableCriterion(_FileCriterion):
    kind: Literal["csv_table"]
    required_columns: list[ColumnName] = Field(min_length=1, max_length=32)
    unique_by: ColumnName
    minimum_rows: int = Field(ge=1, le=10000, strict=True)
    column_equals: dict[ColumnName, ColumnName] = Field(default_factory=dict, max_length=32)

    reference_rows: list[dict[ColumnName, ReferenceValue]] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def validate_table(self) -> Self:
        columns = {normalized_cell(value) for value in self.required_columns}
        filters = {normalized_cell(value) for value in self.column_equals}
        if len(columns) != len(self.required_columns) or len(filters) != len(self.column_equals):
            raise ValueError("Duplicate column names")
        if normalized_cell(self.unique_by) not in columns or not filters <= columns:
            raise ValueError("Key and filter columns must be in required_columns")
        if not self.logical_path.casefold().endswith((".csv", ".tsv")):
            raise ValueError("Expected a CSV or TSV artifact")
        if self.reference_rows and len(self.reference_rows) < self.minimum_rows:
            raise ValueError("Reference data must cover the requested rows")
        keys: set[str] = set()
        size = 0
        for row in self.reference_rows:
            fields = {normalized_cell(key): value for key, value in row.items()}
            if len(fields) != len(row) or fields.keys() != columns:
                raise ValueError("Reference rows must supply all and only the required columns")
            identity = normalized_cell(fields[normalized_cell(self.unique_by)])
            if identity in keys:
                raise ValueError("Duplicate reference key")
            keys.add(identity)
            size += sum(len(value) for value in fields.values())
        if size > 65536:
            raise ValueError("Reference data is too large")
        return self


class ManualCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["manual"]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


GoalCriterion = Annotated[
    NonemptyFileCriterion | CsvTableCriterion | ManualCriterion,
    Field(discriminator="kind"),
]


class GoalEvidenceCheck(BaseModel):
    criterion_index: int
    passed: bool = False
    reason: str
    source_run_id: UUID | None = None
    artifact_id: UUID | None = None
    version_id: UUID | None = None
    version_number: int | None = None
    logical_path: str | None = None
    sha256: str | None = None


class GoalEvidence(BaseModel):
    all_passed: bool
    progress: int
    checks: list[GoalEvidenceCheck]
