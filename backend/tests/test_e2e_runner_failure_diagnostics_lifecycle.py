"""Lifecycle wiring for sanitized failed-Playwright diagnostics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_runner_finalization as finalization  # noqa: E402
import e2e_test_runner as runner  # noqa: E402
from e2e_failure_diagnostics import SourceRejection  # noqa: E402
from e2e_runner_contract import build_e2e_dsns  # noqa: E402
from e2e_runner_export import ExportReceipt  # noqa: E402
from e2e_runner_playwright import PlaywrightOutcome  # noqa: E402
from e2e_runner_process import ProcessResult  # noqa: E402
from e2e_runner_runtime import E2eResources  # noqa: E402
from postgres_runner_runtime import OwnedContainer  # noqa: E402


def _resources(tmp_path: Path) -> E2eResources:
    (tmp_path / "frontend/test-results/scripted-smoke").mkdir(parents=True)
    return E2eResources(
        "run123",
        tmp_path,
        tmp_path.stat().st_dev,
        tmp_path.stat().st_ino,
        OwnedContainer("owner", "run123", "container", "id", 54321),
        set(),
        build_e2e_dsns(password="password", port=54321, database="moldy_e2e_scripted_run123"),
        "160001",
        "m70",
        "m70",
        "fingerprint",
        True,
    )


def _report(*, failed: bool) -> str:
    return json.dumps(
        {
            "suites": [
                {
                    "file": "e2e/chat-error-retry.spec.ts",
                    "specs": [
                        {
                            "title": "retries failed run",
                            "tests": [
                                {
                                    "projectName": "scripted-smoke",
                                    "expectedStatus": "passed",
                                    "results": [
                                        {
                                            "status": "failed",
                                            "errorLocation": {
                                                "file": (
                                                    "/tmp/run/frontend/e2e/chat-error-retry.spec.ts"
                                                ),
                                                "line": 49,
                                                "column": 6,
                                            },
                                        }
                                    ]
                                    if failed
                                    else [],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )


def _cleanup() -> dict[str, bool]:
    return {
        "cleanup_container_removed": True,
        "owned_label_absent": True,
        "postgres_port_removed": True,
        "owned_database_removed": True,
        "backend_port_removed": True,
        "frontend_port_removed": True,
        "proxy_port_removed": True,
        "process_group_stopped": True,
        "cleanup_run_root_removed": True,
        "foreign_containers_preserved": True,
    }


def test_failed_playwright_outcome_reaches_export_finalizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "assert_node22", lambda: None)
    monkeypatch.setattr(runner, "assert_ports_available", lambda _ports: None)
    monkeypatch.setattr(runner, "provision_resources", lambda _lane: _resources(tmp_path))
    monkeypatch.setattr(runner, "cleanup_resources", lambda *_args: _cleanup())
    monkeypatch.setattr(finalization, "publish_runner_receipts", lambda *_args: True)
    calls = 0

    def fake_process(
        _argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> ProcessResult:
        nonlocal calls
        del cwd, env
        calls += 1
        stdout_path.write_text(_report(failed=False) if calls == 1 else "safe failure log")
        stderr_path.write_text("")
        if calls == 2:
            execution = tmp_path / "frontend/test-results/scripted-smoke/execution.json"
            execution.write_text(_report(failed=True))
        return ProcessResult(0 if calls == 1 else 23, True)

    captured: list[PlaywrightOutcome] = []

    def fake_export(*args: object) -> ExportReceipt:
        diagnostics = args[-1]
        assert isinstance(diagnostics, tuple)
        captured.extend(diagnostics)
        return ExportReceipt(False, None, (), (), failure_code="bounds")

    monkeypatch.setattr(runner, "run_owned_process", fake_process)
    monkeypatch.setattr(runner, "export_artifacts", fake_export)

    manifest, exit_code = runner._run("scripted", "scripted-smoke", ())

    assert exit_code == 1
    assert manifest["failure_reason"] == "playwright_failed"
    assert [(item.node_id, item.status) for item in captured] == [
        (
            "scripted-smoke::e2e/chat-error-retry.spec.ts::retries failed run",
            "failed",
        )
    ]
    assert manifest["unexpected_failures"] == [
        {
            "node_id": "scripted-smoke::e2e/chat-error-retry.spec.ts::retries failed run",
            "status": "failed",
            "location": {
                "file": "e2e/chat-error-retry.spec.ts",
                "line": 49,
                "column": 6,
            },
        }
    ]
    export = manifest["export"]
    assert isinstance(export, dict) and export["failure_code"] == "bounds"
    cleanup = manifest["cleanup"]
    assert isinstance(cleanup, dict) and cleanup["cleanup_run_root_removed"] is True


def test_source_rejection_fails_an_otherwise_successful_playwright_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "assert_node22", lambda: None)
    monkeypatch.setattr(runner, "assert_ports_available", lambda _ports: None)
    monkeypatch.setattr(runner, "provision_resources", lambda _lane: _resources(tmp_path))
    monkeypatch.setattr(runner, "cleanup_resources", lambda *_args: _cleanup())
    monkeypatch.setattr(finalization, "publish_runner_receipts", lambda *_args: True)
    calls = 0

    def fake_process(
        _argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> ProcessResult:
        nonlocal calls
        del cwd, env
        calls += 1
        report = _report(failed=False)
        stdout_path.write_text(report)
        stderr_path.write_text("")
        if calls == 2:
            execution = tmp_path / "frontend/test-results/scripted-smoke/execution.json"
            execution.write_text(report)
        return ProcessResult(0, True)

    rejection = SourceRejection(
        "secret_scan",
        "sensitive_assignment",
        "results/execution.log",
        (),
    )
    monkeypatch.setattr(runner, "run_owned_process", fake_process)
    monkeypatch.setattr(
        runner,
        "export_artifacts",
        lambda *_args: ExportReceipt(
            True,
            "output/e2e-captures/safe-rejection",
            (),
            (),
            source_rejection=rejection,
        ),
    )

    manifest, exit_code = runner._run("scripted", "scripted-smoke", ())

    assert exit_code == 1
    assert manifest["status"] == "failed"
    assert manifest["failure_reason"] == "artifact_export_failed"
    assert manifest["child_exit_code"] == 0
    assert manifest["unexpected_failures"] == []
    export = manifest["export"]
    assert isinstance(export, dict)
    assert export["secret_scan_passed"] is True
    assert export["source_rejection"] == {
        "category": "secret_scan",
        "rule_id": "sensitive_assignment",
        "artifact_path": "results/execution.log",
        "tests": [],
    }
