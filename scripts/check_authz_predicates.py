# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.0"]
# ///
# How to run: uv run --directory backend python ../scripts/check_authz_predicates.py
"""Reject new ownership predicates outside the shared authorization package."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class PredicateRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    file: str
    expression: str
    count: int = Field(ge=1)


def _ownership_attribute(node: ast.expr) -> bool:
    """Recognize ORM ownership columns, excluding unrelated instance fields."""
    match node:
        case ast.Attribute(attr="owner_user_id"):
            return True
        case ast.Attribute(value=ast.Name(id=name), attr="user_id"):
            return name[0].isupper()
        case _:
            return False


def scan_source(source: str) -> tuple[str, ...]:
    """Find predicates as syntax, including multiline and reversed comparisons."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        match node:
            case ast.Compare(left=left, ops=ops, comparators=comparators):
                if any(isinstance(op, ast.Eq) for op in ops) and any(
                    _ownership_attribute(value) for value in (left, *comparators)
                ):
                    found.append(ast.unparse(node))
            case ast.Call(func=ast.Attribute(attr="visible_to")):
                found.append(ast.unparse(node))
            case ast.FunctionDef(name="visible_to") | ast.AsyncFunctionDef(name="visible_to"):
                found.append("def visible_to")
    return tuple(found)


def collect_predicates(app_root: Path) -> tuple[PredicateRecord, ...]:
    """Authorization internals are the sole unrestricted ownership seam."""
    found: list[PredicateRecord] = []
    for path in sorted(app_root.rglob("*.py")):
        relative = path.relative_to(app_root)
        if relative.parts[0] == "authz":
            continue
        for expression, count in sorted(Counter(scan_source(path.read_text())).items()):
            found.append(
                PredicateRecord(file=relative.as_posix(), expression=expression, count=count)
            )
    return tuple(found)


def additions(
    current: tuple[PredicateRecord, ...], baseline: tuple[PredicateRecord, ...]
) -> tuple[PredicateRecord, ...]:
    """Deleting old predicates cannot compensate for adding a different one."""
    allowed = {(row.file, row.expression): row.count for row in baseline}
    return tuple(
        row.model_copy(update={"count": row.count - allowed.get((row.file, row.expression), 0)})
        for row in current
        if row.count > allowed.get((row.file, row.expression), 0)
    )


def main() -> int:
    """Inspect this checkout with the reviewed, never auto-increased baseline."""
    root = Path(__file__).resolve().parents[1]
    baseline = TypeAdapter(tuple[PredicateRecord, ...]).validate_json(
        (root / "scripts/authz-predicate-baseline.json").read_text()
    )
    current = collect_predicates(root / "backend/app")
    extra = additions(current, baseline)
    print(f"authz ownership predicates={sum(row.count for row in current)} additions={len(extra)}")
    for row in extra:
        print(f"{row.file}: {row.expression} (+{row.count})")
    return int(bool(extra))


if __name__ == "__main__":
    raise SystemExit(main())
