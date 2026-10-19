"""Skip the tests that read the real, developer-local I1 authority record when it is absent.

The I1 authority chain lives in the gitignored ``.omo/`` record of a developer checkout;
CI checkouts do not have it. Every other task10 test uses private fixtures and still runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LOCAL_AUTHORITY_TESTS = frozenset(
    {
        "test_task10_root_authority.py::test_preedit_inventory_and_registered_legacy_adapter",
        "test_task10_root_authority.py::test_active_scope_and_historical_baseline_are_distinct",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if (ROOT / ".omo").is_dir():
        return
    skip = pytest.mark.skip(reason="local .omo authority record not present (CI checkout)")
    for item in items:
        if f"{item.path.name}::{item.name}" in LOCAL_AUTHORITY_TESTS:
            item.add_marker(skip)
