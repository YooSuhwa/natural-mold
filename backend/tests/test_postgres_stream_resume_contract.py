"""Focused contracts for the disposable PostgreSQL stream-resume lane."""

from __future__ import annotations

import hashlib
import signal
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import postgres_manifest_io  # noqa: E402
import postgres_test_runner  # noqa: E402
from postgres_cleanup_checker import ManifestValidationError, validate_payload  # noqa: E402
from postgres_runner_contract import build_lane_dsns  # noqa: E402
from postgres_runner_runtime import (  # noqa: E402
    ExternalScenarioKind,
    ScenarioKind,
    run_test_child,
)


def _scenario(name: str = "stream-resume") -> dict[str, object]:
    selected = ["tests/integration/test_stream_resume.py::test_resume"]
    receipt = {
        "selected_node_ids": selected,
        "executed_node_ids": selected,
        "deselected_node_ids": [],
        "failed_node_ids": [],
        "skipped_node_ids": [],
        "selected_sha256": hashlib.sha256("\n".join(selected).encode()).hexdigest(),
        "resource_receipt": {
            "database_checked_out": 0,
            "checkpointer_pool_published": False,
            "checkpointer_published": False,
            "conversation_tasks_active": 0,
            "skill_worker_task_active": False,
        },
    }
    return {
        "scenario": name,
        "status": "passed",
        "child_exit_code": 0,
        "image": "postgres:16-alpine",
        "server_version_num": "160010",
        "container_id": "a" * 64,
        "run_id": "b" * 24,
        "container_id_sha256": hashlib.sha256(("a" * 64).encode()).hexdigest(),
        "port": 49152,
        "run_root": "/tmp/.moldy-pg-run-example",
        "run_root_created": True,
        "tmpfs_storage": True,
        "storage_inspected": True,
        "port_mapping_observed": True,
        "run_root_device": 1,
        "run_root_inode": 2,
        "process_id": 999999,
        "process_identity_sha256": "d" * 64,
        "alembic_head": "m63_chat_navigator_indexes",
        "alembic_current": "m63_chat_navigator_indexes",
        "schema_fingerprint": "c" * 64,
        "second_upgrade_idempotent": True,
        "warning_hits": [],
        "warning_scan_complete": True,
        "test_receipt": receipt,
        "cleanup_container_removed": True,
        "owned_label_absent": True,
        "port_mapping_removed": True,
        "process_group_stopped": True,
        "cleanup_run_root_removed": True,
        "foreign_containers_observed": True,
        "foreign_containers_preserved": True,
    }


def _interrupted_scenario() -> dict[str, object]:
    return {
        "scenario": "stream-resume",
        "status": "interrupted",
        "failure_reason": "signal",
        "child_exit_code": 130,
        "image": "postgres:16-alpine",
        "run_id": "b" * 24,
        "run_root_created": True,
        "run_root": "/tmp/.moldy-pg-run-interrupt",
        "tmpfs_storage": False,
        "storage_inspected": False,
        "port_mapping_observed": False,
        "process_id": 999999,
        "process_identity_sha256": "d" * 64,
        "warning_hits": [],
        "warning_scan_complete": False,
        "test_receipt": None,
        "cleanup_container_removed": True,
        "owned_label_absent": True,
        "port_mapping_removed": True,
        "process_group_stopped": True,
        "cleanup_run_root_removed": True,
        "foreign_containers_observed": False,
        "foreign_containers_preserved": None,
    }


def _patch_run_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []
    dummy = postgres_manifest_io.EvidenceDirectory(tmp_path, -1, 1, 1)
    monkeypatch.setattr(postgres_manifest_io, "ensure_evidence_root", lambda _repo: tmp_path)
    monkeypatch.setattr(
        postgres_manifest_io, "validate_manifest_destination", lambda path, _root: path
    )
    monkeypatch.setattr(postgres_manifest_io, "open_evidence_directory", lambda _root: dummy)
    monkeypatch.setattr(postgres_manifest_io, "close_evidence_directory", lambda _root: None)
    monkeypatch.setattr(postgres_manifest_io, "process_identity_sha256", lambda: "d" * 64)
    monkeypatch.setattr(
        postgres_manifest_io,
        "write_manifest",
        lambda _path, payload, _root: captured.append(payload),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["postgres_test_runner.py", "stream-resume", "--manifest", str(tmp_path / "x.json")],
    )
    return captured


def _payload(
    mode: str, scenario: dict[str, object], *, status: str = "passed"
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": mode,
        "status": status,
        "concurrent_pair": False,
        "scenarios": [scenario],
    }


def test_validate_payload_accepts_complete_stream_resume_receipt() -> None:
    validate_payload(_payload("stream-resume", _scenario()))


def test_validate_payload_rejects_stream_resume_with_wrong_scenario() -> None:
    payload = _payload("stream-resume", _scenario("all"))

    with pytest.raises(ManifestValidationError) as caught:
        validate_payload(payload)
    assert str(caught.value) == "stream_resume_scenarios"


def test_validate_payload_accepts_early_stream_resume_interrupt() -> None:
    validate_payload(_payload("stream-resume", _interrupted_scenario(), status="interrupted"))


@pytest.mark.parametrize("mode", ["all", "stream-resume"])
def test_validate_payload_rejects_passed_receipt_with_nonzero_child_exit(mode: str) -> None:
    scenario = _scenario(mode)
    scenario["child_exit_code"] = 23

    with pytest.raises(ManifestValidationError) as caught:
        validate_payload(_payload(mode, scenario))
    assert str(caught.value) == "child_exit_code"


def test_run_cli_dispatches_normal_stream_resume_and_writes_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_run_cli(monkeypatch, tmp_path)
    calls: list[ScenarioKind] = []

    def run_scenario(
        kind: ScenarioKind, *, process_id: int, process_identity: str
    ) -> dict[str, object]:
        del process_id, process_identity
        calls.append(kind)
        return _scenario(kind)

    exit_code = postgres_manifest_io.run_cli(
        run_scenario, postgres_test_runner._early_interrupt_outcome
    )

    assert exit_code == 0
    assert calls == ["stream-resume"]
    assert captured[0]["mode"] == "stream-resume"
    assert captured[0]["scenarios"] == [_scenario()]


def test_run_cli_preserves_stream_resume_mode_after_early_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_run_cli(monkeypatch, tmp_path)

    def interrupted_runner(
        kind: ScenarioKind, *, process_id: int, process_identity: str
    ) -> dict[str, object]:
        del kind, process_id, process_identity
        raise postgres_manifest_io.RunnerInterrupted(signal.SIGINT)

    def early_interrupt(
        signal_number: int,
        *,
        process_id: int,
        process_identity: str,
        mode: ExternalScenarioKind,
    ) -> dict[str, object]:
        return postgres_test_runner._early_interrupt_outcome(
            signal_number,
            process_id=process_id,
            process_identity=process_identity,
            mode=mode,
        )

    exit_code = postgres_manifest_io.run_cli(interrupted_runner, early_interrupt)

    assert exit_code == 130
    assert captured[0]["mode"] == "stream-resume"
    scenarios = captured[0]["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    assert scenario["scenario"] == "stream-resume"


def test_runner_selects_only_stream_resume_integration_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsns = build_lane_dsns(user="runner", password="dummy", port=49152, database="moldy_pg_lane_a1")
    environment = {
        "DATABASE_URL": dsns.async_url,
        "DATABASE_URL_SYNC": dsns.sync_url,
        "INTEGRATION_DATABASE_URL": dsns.integration_url,
    }
    commands: list[list[str]] = []

    def record_command(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("postgres_runner_runtime.run_command", record_command)
    exit_code, _output = run_test_child(environment, "stream-resume")

    assert exit_code == 0
    assert commands == [
        [
            str(BACKEND_ROOT / ".venv/bin/pytest"),
            "-q",
            "-p",
            "tests.postgres_execution_plugin",
            "tests/integration/test_stream_resume.py",
            "-m",
            "integration",
        ]
    ]


def test_stream_resume_collection_contract_has_reviewed_node_set() -> None:
    result = subprocess.run(
        [
            str(BACKEND_ROOT / ".venv/bin/pytest"),
            "-q",
            "-p",
            "tests.postgres_execution_plugin",
            "tests/integration/test_stream_resume.py",
            "-m",
            "integration",
            "--collect-only",
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    nodes = sorted(
        line
        for line in result.stdout.splitlines()
        if line.startswith("tests/integration/test_stream_resume.py::")
    )

    assert result.returncode == 0
    assert len(nodes) == 22
    assert hashlib.sha256("\n".join(nodes).encode()).hexdigest() == (
        "7c566ece21ab2afc6fa488dc9df26b2c7e0c9bb5f6282c5e8566de22c6ef76a6"
    )
