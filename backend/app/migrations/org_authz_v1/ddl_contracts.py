"""Immutable specifications for named organization migration DDL."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class PartialIndexSpec:
    name: str
    table: str
    columns: tuple[str, ...]
    predicate: str


@dataclass(frozen=True, slots=True)
class ForeignKeyReplacement:
    table: str
    column: str
    target: str
    name: str
    ondelete: Literal["RESTRICT", "CASCADE", "SET NULL"] | None


class DdlInvariantError(ValueError):
    """The physical source schema cannot be transformed without guessing."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
