"""Normalize only structured Playwright source locations for safe persistence."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

from e2e_failure_diagnostics import FailureLocation

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

MAX_SOURCE_LOCATION_VALUE: Final = 1_000_000
MAX_SOURCE_LOCATION_PATH: Final = 1024
SAFE_SOURCE_SPEC: Final = re.compile(r"e2e/[A-Za-z0-9][A-Za-z0-9._/-]*\.spec\.ts")


def _location_value(
    value: JsonValue | None, *, present: bool
) -> tuple[bool, FailureLocation | None]:
    if not present:
        return False, None
    if not isinstance(value, dict) or set(value) != {"file", "line", "column"}:
        return True, None
    file = value.get("file")
    line = value.get("line")
    column = value.get("column")
    if (
        not isinstance(file, str)
        or not file
        or len(file) > MAX_SOURCE_LOCATION_PATH
        or type(line) is not int
        or type(column) is not int
        or not 1 <= line <= MAX_SOURCE_LOCATION_VALUE
        or not 1 <= column <= MAX_SOURCE_LOCATION_VALUE
    ):
        return True, None
    normalized = PurePosixPath(file.replace("\\", "/"))
    e2e_indexes = [index for index, part in enumerate(normalized.parts) if part == "e2e"]
    if len(e2e_indexes) != 1:
        return True, None
    relative = PurePosixPath(*normalized.parts[e2e_indexes[0] :]).as_posix()
    if SAFE_SOURCE_SPEC.fullmatch(relative) is None or ".." in PurePosixPath(relative).parts:
        return True, None
    return True, FailureLocation(relative, line, column)


def extract_failure_location(
    result: dict[str, JsonValue], expected_spec: str
) -> FailureLocation | None:
    """Return one agreed safe location bound to the failed node's exact spec."""
    direct_present, direct = _location_value(
        result.get("errorLocation"), present="errorLocation" in result
    )
    error = result.get("error")
    nested_present = isinstance(error, dict) and "location" in error
    nested_value = error.get("location") if isinstance(error, dict) else None
    nested_present, nested = _location_value(nested_value, present=nested_present)
    present = [
        location
        for is_present, location in (
            (direct_present, direct),
            (nested_present, nested),
        )
        if is_present
    ]
    if not present or any(location is None for location in present):
        return None
    first = present[0]
    if not all(location == first for location in present):
        return None
    return first if first is not None and first.file == expected_spec else None
