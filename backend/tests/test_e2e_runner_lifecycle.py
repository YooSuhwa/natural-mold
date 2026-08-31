"""Lifecycle outcomes for the isolated E2E runner using narrow process fakes."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_test_runner as runner  # noqa: E402
from e2e_runner_contract import build_e2e_dsns  # noqa: E402
from e2e_runner_export import ExportFile, ExportReceipt  # noqa: E402
from e2e_runner_process import ProcessResult  # noqa: E402
from e2e_runner_runtime import E2eResources, ProvisioningError  # noqa: E402
from e2e_runner_scenario import cleanup_passed, live_egress_passed  # noqa: E402
from postgres_manifest_io import RunnerInterrupted  # noqa: E402
from postgres_runner_runtime import OwnedContainer  # noqa: E402


def _resources(tmp_path: Path) -> E2eResources:
    (tmp_path / "frontend/test-results/scripted-smoke").mkdir(parents=True)
    return E2eResources(
        "run123",
        tmp_path,
        tmp_path.stat().st_dev,
        tmp_path.stat().st_ino,
        OwnedContainer("owner", "run123", "container", "id", 54321),
        {"foreign"},
        build_e2e_dsns(
            password="password",
            port=54321,
            database="moldy_e2e_scripted_run123",
        ),
        "160001",
        "m70",
        "m70",
        "fingerprint",
        True,
    )


def _report() -> str:
    return json.dumps(
        {
            "suites": [
                {
                    "file": "/mirror/frontend/e2e/smoke.spec.ts",
                    "specs": [
                        {
                            "title": "smoke works",
                            "tests": [{"projectName": "scripted-smoke", "results": []}],
                        }
                    ],
                }
            ]
        }
    )


def _write_receipt(argv: list[str], stdout_path: Path, stderr_path: Path, tmp_path: Path) -> None:
    stdout_path.write_text(_report() if "--reporter=json" in argv else "")
    stderr_path.write_text("")
    if "--reporter=json" not in argv:
        (tmp_path / "frontend/test-results/scripted-smoke/execution.json").write_text(_report())


def _manifest_section(manifest: dict[str, object], name: str) -> dict[str, object]:
    value = manifest[name]
    assert isinstance(value, dict)
    return value


def _install_common_fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "assert_node22", lambda: None)
    monkeypatch.setattr(runner, "assert_ports_available", lambda _ports: None)
    monkeypatch.setattr(runner, "provision_resources", lambda _lane: _resources(tmp_path))
    monkeypatch.setattr(
        runner,
        "export_artifacts",
        lambda *_args: ExportReceipt(
            True,
            "output/e2e-captures/test",
            (ExportFile("execution.json", "0" * 64, 10),),
            (),
        ),
    )
    monkeypatch.setattr(
        runner,
        "cleanup_resources",
        lambda *_args: {
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
        },
    )


def test_success_records_same_selected_and_executed_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given two green children, when run, then the manifest is a no-retry pass."""
    _install_common_fakes(monkeypatch, tmp_path)

    def fake_process(
        _argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> ProcessResult:
        del cwd, env
        _write_receipt(_argv, stdout_path, stderr_path, tmp_path)
        return ProcessResult(0, True)

    monkeypatch.setattr(runner, "run_owned_process", fake_process)

    manifest, exit_code = runner._run("scripted", "scripted-smoke", ())

    assert exit_code == 0
    assert manifest["status"] == "passed"
    assert manifest["selected_ids"] == manifest["executed_ids"]


def test_child_failure_still_exports_and_cleans(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given a failing execution, when run, then failure and complete cleanup remain visible."""
    _install_common_fakes(monkeypatch, tmp_path)
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
        _write_receipt(_argv, stdout_path, stderr_path, tmp_path)
        return ProcessResult(0 if calls == 1 else 23, True)

    monkeypatch.setattr(runner, "run_owned_process", fake_process)

    manifest, exit_code = runner._run("scripted", "scripted-smoke", ())

    assert exit_code == 1
    assert manifest["failure_reason"] == "playwright_failed"
    assert _manifest_section(manifest, "cleanup")["cleanup_run_root_removed"] is True
    assert _manifest_section(manifest, "export")["secret_scan_passed"] is True


def test_export_failure_does_not_overwrite_playwright_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_common_fakes(monkeypatch, tmp_path)
    calls = 0

    def fake_process(
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> ProcessResult:
        nonlocal calls
        del cwd, env
        calls += 1
        _write_receipt(argv, stdout_path, stderr_path, tmp_path)
        return ProcessResult(0 if calls == 1 else 23, True)

    monkeypatch.setattr(runner, "run_owned_process", fake_process)
    monkeypatch.setattr(
        runner,
        "export_artifacts",
        lambda *_args: ExportReceipt(False, None, (), (), failure_code="exporter_process_failed"),
    )

    manifest, exit_code = runner._run("scripted", "scripted-smoke", ())

    assert exit_code == 1
    assert manifest["failure_reason"] == "playwright_failed"
    assert _manifest_section(manifest, "export")["failure_code"] == "exporter_process_failed"


def test_node_guard_fails_before_resource_provision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provisioned = False

    def provision(_lane: str) -> None:
        nonlocal provisioned
        provisioned = True

    monkeypatch.setattr(
        runner,
        "assert_node22",
        lambda: (_ for _ in ()).throw(runner.E2eContractError("node_major_mismatch")),
    )
    monkeypatch.setattr(runner, "provision_resources", provision)

    manifest, exit_code = runner._run("scripted", "scripted-smoke", ())

    assert exit_code == 1
    assert manifest["failure_reason"] == "node_major_mismatch"
    assert provisioned is False


def test_signal_stops_child_then_exports_and_cleans(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given SIGINT during execution, when handled, then exit 130 retains cleanup receipt."""
    _install_common_fakes(monkeypatch, tmp_path)
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
        if calls == 2:
            raise RunnerInterrupted(signal.SIGINT)
        _write_receipt(_argv, stdout_path, stderr_path, tmp_path)
        return ProcessResult(0, True)

    monkeypatch.setattr(runner, "run_owned_process", fake_process)

    manifest, exit_code = runner._run("scripted", "scripted-smoke", ())

    assert exit_code == 130
    assert manifest["status"] == "interrupted"
    assert _manifest_section(manifest, "cleanup")["process_group_stopped"] is True


def test_owned_run_requires_observed_foreign_resource_preservation() -> None:
    """Given acquired ownership, when Docker observation is unknown, then cleanup fails closed."""
    cleanup = {"cleanup_run_root_removed": True, "foreign_containers_preserved": None}

    assert cleanup_passed(cleanup, resources_acquired=True) is False
    assert cleanup_passed(cleanup, resources_acquired=False) is True


def test_live_success_requires_allowed_chat_completion_receipt() -> None:
    """Given live egress facts, when checked, then only an allowed successful POST is green."""
    denied = {"enabled": True, "clean_stop": True, "records": []}
    allowed = {
        "enabled": True,
        "clean_stop": True,
        "records": [
            {
                "method": "POST",
                "origin": "https://gateway.invalid",
                "path_class": "chat_completions",
                "status": 200,
                "count": 1,
            }
        ],
    }

    assert live_egress_passed(denied) is False
    assert live_egress_passed(allowed) is True


def test_provisioning_sigint_preserves_cleanup_and_interrupt_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "assert_ports_available", lambda _ports: None)
    monkeypatch.setattr(runner, "assert_node22", lambda: None)
    cleanup: dict[str, bool | None] = {
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
    ownership = {
        "run_root": True,
        "database": False,
        "backend": False,
        "frontend": False,
        "proxy": False,
    }

    def interrupt(_lane: str) -> None:
        raise ProvisioningError("RunnerInterrupted", cleanup, ownership, signal.SIGINT)

    monkeypatch.setattr(runner, "provision_resources", interrupt)

    manifest, exit_code = runner._run("scripted", "scripted-smoke", ())

    assert exit_code == 130
    assert manifest["status"] == "interrupted"
    assert manifest["failure_reason"] == "signal"
    assert manifest["cleanup"] == cleanup
