"""Rule conditions shared by workflow decisions and tool approval rules (approval-modes-plan 3.4).

A condition is ``{"all": [clauses]}`` or ``{"any": [clauses]}``; a clause compares the value at
``source`` + ``path`` (a JSON pointer) with ``value``. Evaluation is pure and deterministic (no
time, randomness or outside calls), so an approval predicate gives the same answer when a run
resumes. A missing value never raises: it compares as absent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Operator = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists", "starts_with", "ends_with"
]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
Pointer = Annotated[str, Field(pattern=r"^(/[^/]{1,64}){0,8}$")]
_MISSING: Final = object()
_TEXT_OPS: Final = ("contains", "starts_with", "ends_with")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Clause(_Strict):
    source: Identifier
    path: Pointer = ""
    op: Operator
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def _value_matches_op(self) -> Clause:
        if self.op == "exists" and self.value is not None:
            raise ValueError("exists takes no value")
        if self.op in ("gt", "gte", "lt", "lte") and (
            isinstance(self.value, bool) or not isinstance(self.value, int | float)
        ):
            raise ValueError(f"{self.op} needs a number")
        if self.op in _TEXT_OPS and not isinstance(self.value, str):
            raise ValueError(f"{self.op} needs text")
        return self


class Condition(_Strict):
    all: Annotated[list[Clause], Field(min_length=1, max_length=20)] | None = None
    any: Annotated[list[Clause], Field(min_length=1, max_length=20)] | None = None

    @model_validator(mode="after")
    def _one_group(self) -> Condition:
        if (self.all is None) == (self.any is None):
            raise ValueError("a condition is either all or any")
        return self

    @property
    def clauses(self) -> list[Clause]:
        return self.all or self.any or []


def resolve(values: Mapping[str, Any], node: str, path: str) -> Any:
    """The value at ``path`` (a JSON pointer) of ``values[node]``; ``None`` if absent."""
    current: Any = values.get(node, _MISSING)
    for part in path.split("/")[1:]:
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return None if current is _MISSING else current


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def equal(left: Any, right: Any) -> bool:
    """Numbers compare by value (1 == 1.0); a boolean only equals a boolean."""
    left_number, right_number = _number(left), _number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    return left == right


def clause_holds(clause: Clause, values: Mapping[str, Any]) -> bool:
    value = resolve(values, clause.source, clause.path)
    if clause.op == "exists":
        return value is not None
    if clause.op == "eq":
        return equal(value, clause.value)
    if clause.op == "ne":
        return not equal(value, clause.value)
    if clause.op == "contains":
        if isinstance(value, str):
            return str(clause.value) in value
        if isinstance(value, list):
            return clause.value in value
        return False
    if clause.op == "starts_with":
        return isinstance(value, str) and value.startswith(str(clause.value))
    if clause.op == "ends_with":
        return isinstance(value, str) and value.endswith(str(clause.value))
    left, right = _number(value), _number(clause.value)
    if left is None or right is None:
        return False
    return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[
        clause.op
    ]


def holds(condition: Condition, values: Mapping[str, Any]) -> bool:
    results = [clause_holds(clause, values) for clause in condition.clauses]
    return all(results) if condition.all is not None else any(results)


__all__ = [
    "Clause",
    "Condition",
    "Identifier",
    "Operator",
    "Pointer",
    "clause_holds",
    "equal",
    "holds",
    "resolve",
]
