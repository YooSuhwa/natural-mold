"""JSON Schema validation for the merged catalog.

Wraps ``jsonschema`` so the build pipeline can reject malformed catalogs
*before* they overwrite the on-disk artifact. The atomic-write rule in
``loaders`` plus this gate means a botched build keeps the previous
catalog live until the next successful run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "model_catalog" / "schema.json"
)

_schema_cache: dict[str, Any] | None = None


def _load_schema() -> dict[str, Any]:
    global _schema_cache
    if _schema_cache is None:
        with _SCHEMA_PATH.open(encoding="utf-8") as f:
            _schema_cache = json.load(f)
    assert _schema_cache is not None  # noqa: S101 — set in the branch above (type narrowing)
    return _schema_cache


def reset_schema_cache() -> None:
    """For tests — drop the cached schema so the next call re-reads the file."""

    global _schema_cache
    _schema_cache = None


def validate_catalog(catalog: dict[str, Any]) -> list[str]:
    """Return a list of error strings (empty = catalog is valid)."""

    schema = _load_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(catalog), key=lambda e: list(e.absolute_path))
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors
    ]
