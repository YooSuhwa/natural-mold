"""Small, read-only helpers for reviewed runtime contract fixtures."""

from __future__ import annotations

import json
from pathlib import Path

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]

_FIXTURE_DIR = Path(__file__).with_name("fixtures")


def load_contract_fixture(name: str) -> dict[str, JSONValue]:
    """Load a checked fixture without offering an auto-update path."""

    value = json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("runtime contract fixture must contain a JSON object")
    return value


def contract_diff_paths(
    expected: JSONValue,
    actual: JSONValue,
    *,
    path: str = "$",
) -> list[str]:
    """Return stable JSON paths whose values or shapes differ."""

    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[str] = []
        for key in sorted(expected.keys() | actual.keys()):
            child_path = f"{path}.{key}"
            if key not in expected or key not in actual:
                differences.append(child_path)
                continue
            differences.extend(contract_diff_paths(expected[key], actual[key], path=child_path))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        differences = []
        if len(expected) != len(actual):
            differences.append(f"{path}.length")
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=False)):
            differences.extend(
                contract_diff_paths(expected_item, actual_item, path=f"{path}[{index}]")
            )
        return differences
    return [] if expected == actual and type(expected) is type(actual) else [path]


def assert_contract_matches(name: str, actual: dict[str, JSONValue]) -> None:
    """Compare an observed manifest with its manually reviewed fixture."""

    expected = load_contract_fixture(name)
    differences = contract_diff_paths(expected, actual)
    assert not differences, f"contract drift at: {', '.join(differences[:20])}"
