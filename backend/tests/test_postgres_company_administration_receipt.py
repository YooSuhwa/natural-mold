"""Company-administration manifests bind the receipt to the fixed native registry."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import postgres_manifest_io  # noqa: E402
import postgres_test_runner  # noqa: E402
from postgres_cleanup_checker import ManifestValidationError, validate_payload  # noqa: E402
from postgres_company_suite import (  # noqa: E402
    COMPANY_ADMINISTRATION_LEGACY_NODES,
    COMPANY_ADMINISTRATION_MODULES,
)
from postgres_runner_runtime import ScenarioKind  # noqa: E402

from tests.test_postgres_stream_resume_contract import (  # noqa: E402
    _patch_run_cli,
    _payload,
    _scenario,
)

MODE = "company-administration"


def _company_scenario(selected: list[str]) -> dict[str, object]:
    scenario = _scenario(MODE)
    receipt = scenario["test_receipt"]
    assert isinstance(receipt, dict)
    receipt.update(
        {
            "selected_node_ids": selected,
            "executed_node_ids": selected,
            "selected_sha256": hashlib.sha256("\n".join(selected).encode()).hexdigest(),
        }
    )
    return scenario


def _complete_selection() -> list[str]:
    modules = [f"{module}::test_case" for module in COMPANY_ADMINISTRATION_MODULES]
    return sorted(modules + list(COMPANY_ADMINISTRATION_LEGACY_NODES))


def test_validate_payload_accepts_complete_company_registry_receipt() -> None:
    validate_payload(_payload(MODE, _company_scenario(_complete_selection())))


@pytest.mark.parametrize("drop", [0, -1])
def test_validate_payload_rejects_receipt_missing_a_registered_target(drop: int) -> None:
    selected = _complete_selection()
    del selected[drop]
    with pytest.raises(ManifestValidationError) as caught:
        validate_payload(_payload(MODE, _company_scenario(selected)))
    assert str(caught.value) == "company_selection"


def test_validate_payload_rejects_foreign_or_wrong_scenario() -> None:
    foreign = [*_complete_selection(), "tests/integration/test_stream_resume.py::test_resume"]
    with pytest.raises(ManifestValidationError) as caught:
        validate_payload(_payload(MODE, _company_scenario(sorted(foreign))))
    assert str(caught.value) == "company_selection"
    with pytest.raises(ManifestValidationError) as wrong:
        validate_payload(_payload(MODE, _scenario("all")))
    assert str(wrong.value) == "company_administration_scenarios"


def test_run_cli_dispatches_company_mode_and_writes_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_run_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["postgres_test_runner.py", MODE, "--manifest", str(tmp_path / "x.json")]
    )
    calls: list[ScenarioKind] = []

    def run_scenario(
        kind: ScenarioKind, *, process_id: int, process_identity: str
    ) -> dict[str, object]:
        del process_id, process_identity
        calls.append(kind)
        return _company_scenario(_complete_selection())

    exit_code = postgres_manifest_io.run_cli(
        run_scenario, postgres_test_runner._early_interrupt_outcome
    )

    assert (exit_code, calls, captured[0]["mode"]) == (0, [MODE], MODE)
