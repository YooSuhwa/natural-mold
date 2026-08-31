"""Ensure the reviewed runtime facade fixture rejects deliberate drift."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

import pytest

from tests.agent_runtime.runtime_contract_helpers import (
    assert_contract_matches,
    contract_diff_paths,
    load_contract_fixture,
)
from tests.agent_runtime.test_runtime_facade_contract import (
    _FIXTURE_NAME,
    collect_runtime_facade_contract,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        (
            lambda manifest: manifest["signatures"]["build_agent"]["parameters"][0].update(
                {"name": "mutated_model"}
            ),
            "$.signatures.build_agent.parameters[0].name",
        ),
        (
            lambda manifest: manifest["build_agent"]["middleware_names"].__setitem__(
                slice(0, 2), ["TodoListMiddleware", "FilesystemMiddleware"]
            ),
            "$.build_agent.middleware_names[0]",
        ),
        (
            lambda manifest: manifest["build_agent"]["filesystem_tools"].append("delete"),
            "$.build_agent.filesystem_tools.length",
        ),
    ],
)
async def test_runtime_facade_validator_identifies_deliberate_drift(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[dict[str, Any]], None],
    expected_path: str,
) -> None:
    """Given a contract mutation, validation names the affected checked JSON path."""

    manifest = await collect_runtime_facade_contract(monkeypatch)
    mutated = copy.deepcopy(manifest)
    mutation(mutated)

    differences = contract_diff_paths(load_contract_fixture(_FIXTURE_NAME), mutated)

    assert expected_path in differences
    with pytest.raises(AssertionError, match=re.escape(expected_path)):
        assert_contract_matches(_FIXTURE_NAME, mutated)
