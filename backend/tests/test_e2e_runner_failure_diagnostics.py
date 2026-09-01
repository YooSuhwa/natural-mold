"""Contracts for sanitized failed-Playwright E2E diagnostics."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_failure_diagnostics as diagnostics  # noqa: E402
import e2e_runner_playwright as playwright  # noqa: E402
from e2e_cleanup_export import validate_export  # noqa: E402
from postgres_cleanup_checker import ManifestValidationError  # noqa: E402


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_fallback_export(repository: Path, rejection: dict[str, object]) -> dict[str, object]:
    """Write one manifest-only persistent export and return its runner receipt."""
    (repository / ".gitignore").write_text("output/\n")
    relative = "output/e2e-captures/20260901-failed-e2e"
    directory = repository / relative
    directory.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "project": "scripted-full",
        "policy": {
            "version": 1,
            "screenshots": "scripted-capture-only",
            "max_file_bytes": 20 * 1024 * 1024,
            "max_total_bytes": 50 * 1024 * 1024,
        },
        "secret_scan": {"passed": True, "exact_secret_count": 0},
        "source_rejection": rejection,
        "files": [],
        "total": {"file_count": 0, "size_bytes": 0},
    }
    content = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (directory / "export-manifest.json").write_bytes(content)
    manifest_file = {
        "path": "export-manifest.json",
        "sha256": _sha256(content),
        "size_bytes": len(content),
    }
    return {
        "schema_version": 1,
        "secret_scan_passed": True,
        "export_directory": relative,
        "manifest": manifest_file,
        "files": [manifest_file],
        "screenshots": [],
        "source_rejection": rejection,
    }


def _source_rejection(*, rule_id: str = "bearer_token") -> dict[str, object]:
    return {
        "category": "secret_scan",
        "rule_id": rule_id,
        "artifact_path": "results/execution.log",
        "tests": [
            {
                "node_id": "scripted-full::e2e/chat-error-retry.spec.ts::retries failed run",
                "status": "failed",
            }
        ],
    }


def _execution_receipt(tests: list[dict[str, object]]) -> dict[str, object]:
    return {
        "suites": [
            {
                "file": "e2e/chat-error-retry.spec.ts",
                "specs": [{"title": "retries failed run", "tests": tests}],
            }
        ]
    }


def _set_untrusted_status(rejection: dict[str, object]) -> None:
    """Mutate one sanitized test entry into an invalid terminal status."""
    tests = rejection["tests"]
    assert isinstance(tests, list) and len(tests) == 1
    entry = tests[0]
    assert isinstance(entry, dict)
    entry["status"] = "passed"


def test_execution_parse_reports_only_unexpected_terminal_failures(tmp_path: Path) -> None:
    # Given actual and expected Playwright failures in one execution receipt.
    receipt = tmp_path / "execution.json"
    receipt.write_text(
        json.dumps(
            _execution_receipt(
                [
                    {
                        "title": "retries failed run",
                        "projectName": "scripted-full",
                        "expectedStatus": "passed",
                        "results": [
                            {
                                "status": "failed",
                                "errorLocation": {
                                    "file": (
                                        "/private/tmp/run/frontend/e2e/chat-error-retry.spec.ts"
                                    ),
                                    "line": 49,
                                    "column": 6,
                                },
                                "error": {
                                    "location": {
                                        "file": (
                                            "/private/tmp/run/frontend/e2e/chat-error-retry.spec.ts"
                                        ),
                                        "line": 49,
                                        "column": 6,
                                    },
                                    "message": "api_key=must-not-survive",
                                    "stack": "Bearer must-not-survive",
                                },
                                "stdout": ["must-not-survive"],
                                "stderr": ["must-not-survive"],
                            }
                        ],
                    },
                    {
                        "title": "known expected failure",
                        "projectName": "scripted-full",
                        "expectedStatus": "failed",
                        "results": [{"status": "failed"}],
                    },
                ]
            )
        )
    )

    # When the richer execution parser reads the receipt.
    execution = playwright.parse_playwright_execution_json(receipt)

    # Then only the unexpected failure can enter sanitized export diagnostics.
    assert len(execution.nodes) == 2
    assert [
        (item.node_id, item.status, item.location) for item in execution.unexpected_outcomes
    ] == [
        (
            "scripted-full::e2e/chat-error-retry.spec.ts::retries failed run",
            "failed",
            diagnostics.FailureLocation("e2e/chat-error-retry.spec.ts", 49, 6),
        )
    ]
    assert "must-not-survive" not in repr(execution.unexpected_outcomes)


@pytest.mark.parametrize(
    "locations",
    [
        {"errorLocation": {"file": "../secret.spec.ts", "line": 1, "column": 1}},
        {"errorLocation": {"file": "e2e/a.spec.ts", "line": True, "column": 1}},
        {"errorLocation": {"file": "e2e/a.spec.ts", "line": 0, "column": 1}},
        {"errorLocation": {"file": "e2e/a.spec.ts", "line": 1_000_001, "column": 1}},
        {
            "errorLocation": {
                "file": "e2e/other-safe.spec.ts",
                "line": 1,
                "column": 1,
            }
        },
        {
            "errorLocation": {
                "file": "/tmp/e2e/archive/e2e/chat-error-retry.spec.ts",
                "line": 1,
                "column": 1,
            }
        },
        {
            "errorLocation": {"file": "e2e/a.spec.ts", "line": 1, "column": 1},
            "error": {"location": {"file": "e2e/a.spec.ts", "line": 2, "column": 1}},
        },
    ],
)
def test_execution_parse_omits_unsafe_or_conflicting_failure_location(
    tmp_path: Path, locations: dict[str, object]
) -> None:
    receipt = tmp_path / "execution.json"
    result = {"status": "failed", **locations}
    receipt.write_text(
        json.dumps(
            _execution_receipt(
                [
                    {
                        "title": "retries failed run",
                        "projectName": "scripted-full",
                        "expectedStatus": "passed",
                        "results": [result],
                    }
                ]
            )
        )
    )

    execution = playwright.parse_playwright_execution_json(receipt)

    assert len(execution.unexpected_outcomes) == 1
    assert execution.unexpected_outcomes[0].location is None


def test_failure_diagnostic_serializer_rejects_location_for_another_safe_spec() -> None:
    node_id = "scripted-full::e2e/a.spec.ts::fails"
    outcome = diagnostics.FailureDiagnostic(
        node_id,
        "failed",
        diagnostics.FailureLocation("e2e/b.spec.ts", 1, 1),
    )

    with pytest.raises(diagnostics.FailureDiagnosticError):
        diagnostics.failure_diagnostics_payload((outcome,), "scripted-full")


def test_execution_parse_bounds_unexpected_failure_diagnostics(tmp_path: Path) -> None:
    # Given more unexpected results than the failure-export policy permits.
    receipt = tmp_path / "execution.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/failure.spec.ts",
                        "specs": [
                            {
                                "title": f"failure {index}",
                                "tests": [
                                    {
                                        "projectName": "scripted-full",
                                        "expectedStatus": "passed",
                                        "results": [{"status": "failed"}],
                                    }
                                ],
                            }
                            for index in range(17)
                        ],
                    }
                ]
            }
        )
    )

    # When diagnostics are parsed.
    with pytest.raises(playwright.PlaywrightReceiptError, match="unexpected_outcome_limit"):
        playwright.parse_playwright_execution_json(receipt)

    # Then unbounded failed-test metadata cannot reach the persistent export.


def test_validate_export_accepts_manifest_only_sanitized_source_rejection(tmp_path: Path) -> None:
    # Given an empty export whose manifest records only allowlisted failure metadata.
    export = _write_fallback_export(tmp_path, _source_rejection())

    # When the independent export checker validates it.
    validate_export(export, "scripted-full", tmp_path)

    # Then the failed run retains safe diagnostics without retaining raw artifacts.


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rejection: rejection.update({"rule_id": "untrusted_pattern"}),
        lambda rejection: rejection.update({"artifact_path": "../execution.log"}),
        lambda rejection: rejection.update({"artifact_path": "."}),
        lambda rejection: rejection.update({"artifact_path": "results"}),
        lambda rejection: rejection.update({"artifact_path": f"results/{'a' * 129}.log"}),
        lambda rejection: rejection.update({"artifact_path": "results/execution\n.log"}),
        lambda rejection: rejection.update({"artifact_path": "results/customer secret.log"}),
        lambda rejection: rejection.update({"extra": "forbidden"}),
        _set_untrusted_status,
        lambda rejection: rejection.update(
            {
                "tests": [
                    {
                        "node_id": f"scripted-full::e2e/failure.spec.ts::failure {index}",
                        "status": "failed",
                    }
                    for index in range(17)
                ]
            }
        ),
    ],
)
def test_validate_export_rejects_untrusted_source_rejection_metadata(
    tmp_path: Path, mutate: Callable[[dict[str, object]], None]
) -> None:
    # Given one malformed source-rejection field or an excessive diagnostic list.
    rejection = _source_rejection()
    mutate(rejection)
    export = _write_fallback_export(tmp_path, rejection)

    # When the checker reads the persisted manifest-only export.
    with pytest.raises(ManifestValidationError):
        validate_export(export, "scripted-full", tmp_path)

    # Then unknown rules, unsafe paths, extra fields, and untrusted diagnostics fail closed.


@pytest.mark.parametrize("mutation", ["omitted", "mismatch"])
def test_validate_export_requires_receipt_manifest_rejection_parity(
    tmp_path: Path, mutation: str
) -> None:
    rejection = _source_rejection()
    export = _write_fallback_export(tmp_path, rejection)
    if mutation == "omitted":
        del export["source_rejection"]
    else:
        receipt_rejection = export["source_rejection"]
        assert isinstance(receipt_rejection, dict)
        receipt_rejection["rule_id"] = "jwt"

    with pytest.raises(ManifestValidationError):
        validate_export(export, "scripted-full", tmp_path)


def test_validate_export_rejects_source_rejection_with_persisted_raw_artifact(
    tmp_path: Path,
) -> None:
    # Given a source-rejection manifest that also declares an otherwise valid raw artifact.
    rejection = _source_rejection()
    export = _write_fallback_export(tmp_path, rejection)
    directory = tmp_path / str(export["export_directory"])
    raw = b"failed playwright output"
    (directory / "results").mkdir()
    (directory / "results/execution.log").write_bytes(raw)
    artifact = {
        "path": "results/execution.log",
        "sha256": _sha256(raw),
        "size_bytes": len(raw),
    }
    manifest = json.loads((directory / "export-manifest.json").read_text())
    manifest["files"] = [artifact]
    manifest["total"] = {"file_count": 1, "size_bytes": len(raw)}
    content = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (directory / "export-manifest.json").write_bytes(content)
    manifest_file = {
        "path": "export-manifest.json",
        "sha256": _sha256(content),
        "size_bytes": len(content),
    }
    export["manifest"] = manifest_file
    export["files"] = [manifest_file, artifact]

    # When the persistent export is validated.
    with pytest.raises(ManifestValidationError):
        validate_export(export, "scripted-full", tmp_path)

    # Then manifest-only fallback cannot accidentally retain a raw diagnostic file.
