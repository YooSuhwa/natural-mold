"""Behavior contracts for the disposable PostgreSQL runner."""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import postgres_runner_cleanup  # noqa: E402
import postgres_test_runner  # noqa: E402
from postgres_manifest_io import on_signal  # noqa: E402
from postgres_runner_contract import (  # noqa: E402
    DsnContractError,
    build_lane_dsns,
    parse_lane_dsns,
)
from postgres_runner_runtime import (  # noqa: E402
    OwnedContainer,
    build_docker_env,
    build_docker_run_argv,
    lane_env,
    run_test_child,
)
from postgres_test_runner import _run_scenario  # noqa: E402


def test_lane_dsns_round_trip_when_generated_for_loopback() -> None:
    # Given one generated lane database target.
    raw = build_lane_dsns(
        user="runner", password="dummy-value", port=49152, database="moldy_pg_lane_a1"
    )

    # When all three URLs cross the parser boundary.
    parsed = parse_lane_dsns(raw)

    # Then their non-driver target identity is exactly the same.
    assert parsed.host == "127.0.0.1"
    assert parsed.port == 49152
    assert parsed.database == "moldy_pg_lane_a1"


@pytest.mark.parametrize(
    ("field", "replacement", "reason"),
    [
        ("async_url", "postgresql://runner:x@127.0.0.1:49152/moldy_pg_lane_a1", "async_driver"),
        (
            "sync_url",
            "postgresql+asyncpg://runner:x@127.0.0.1:49152/moldy_pg_lane_a1",
            "sync_driver",
        ),
        (
            "integration_url",
            "postgresql://runner:x@127.0.0.1:49152/moldy_pg_lane_a1",
            "integration_driver",
        ),
        (
            "async_url",
            "postgresql+asyncpg://runner:x@localhost:49152/moldy_pg_lane_a1",
            "target_mismatch",
        ),
        ("async_url", "postgresql+asyncpg://runner:x@127.0.0.1:49152/moldy", "unsafe_database"),
        (
            "async_url",
            "postgresql+asyncpg://runner:x@127.0.0.1:49152/moldy_pg_lane_a1?q=1",
            "url_options",
        ),
    ],
)
def test_lane_dsn_rejects_unsafe_or_mismatched_input_without_url(
    field: str,
    replacement: str,
    reason: str,
) -> None:
    # Given valid generated URLs with one untrusted field replaced.
    raw = build_lane_dsns(user="runner", password="x", port=49152, database="moldy_pg_lane_a1")
    broken = raw._replace(**{field: replacement})

    # When the boundary parser rejects the input.
    with pytest.raises(DsnContractError) as caught:
        parse_lane_dsns(broken)

    # Then only a stable reason code is exposed.
    assert str(caught.value) == reason
    assert "postgresql" not in str(caught.value)


def test_lane_env_does_not_inherit_provider_credentials(tmp_path: Path, monkeypatch) -> None:
    # Given a provider credential in the parent environment.
    monkeypatch.setenv("OPENAI_API_KEY", "canary-must-not-cross")
    dsns = build_lane_dsns(user="runner", password="dummy", port=49152, database="moldy_pg_lane_a1")

    # When the child environment is built.
    child = lane_env(dsns, tmp_path, tmp_path / "receipt.json")

    # Then the canary is absent while internally generated DSNs remain.
    assert "OPENAI_API_KEY" not in child
    assert child["DATABASE_URL"] == dsns.async_url


def test_docker_argv_uses_environment_reference_not_password_value() -> None:
    # Given an owned container identity and a real password canary.
    owner = OwnedContainer("private-owner", "public-run", "owned-name")
    password = "database-password-canary"

    # When Docker receives its argv and narrowly scoped environment.
    argv = build_docker_run_argv(owner)
    environment = build_docker_env(password)

    # Then the secret crosses only the Docker-client environment boundary.
    assert "POSTGRES_PASSWORD" in argv
    assert all(password not in argument for argument in argv)
    assert not any(argument.startswith("POSTGRES_PASSWORD=") for argument in argv)
    assert environment["POSTGRES_PASSWORD"] == password


def test_docker_env_does_not_inherit_provider_key(monkeypatch) -> None:
    # Given an unrelated provider key in the parent environment.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-canary")

    # When the Docker client environment is built.
    child = build_docker_env("database-dummy")

    # Then only the database bootstrap secret crosses this boundary.
    assert "ANTHROPIC_API_KEY" not in child
    assert child["POSTGRES_PASSWORD"] == "database-dummy"


def test_runner_selects_all_marked_integration_nodes_not_only_directory(monkeypatch) -> None:
    # Given a PostgreSQL lane environment and a process adapter that records its argv.
    dsns = build_lane_dsns(user="runner", password="dummy", port=49152, database="moldy_pg_lane_a1")
    environment = {
        "DATABASE_URL": dsns.async_url,
        "DATABASE_URL_SYNC": dsns.sync_url,
        "INTEGRATION_DATABASE_URL": dsns.integration_url,
    }
    commands: list[list[str]] = []

    def record_command(argv: list[str], **_kwargs: object):
        commands.append(argv)
        import subprocess

        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("postgres_runner_runtime.run_command", record_command)

    # When the canonical all scenario builds the pytest invocation.
    exit_code, _output = run_test_child(environment, "all")

    # Then it selects all repository integration markers, including nodes outside tests/integration.
    assert exit_code == 0
    assert commands[0][-3:] == ["tests", "-m", "integration"]
    assert "tests/integration" not in commands[0]


def test_parent_signal_after_container_create_emits_progressive_cleanup_receipt(
    monkeypatch,
) -> None:
    # Given a no-Docker runner whose parent signal lands after Docker returns an owned identity.
    container_id = "a" * 64
    captured_owner: list[OwnedContainer] = []
    monkeypatch.setattr(postgres_test_runner, "start_process_scope", lambda: None)
    monkeypatch.setattr(postgres_test_runner, "docker_ids", lambda: {"foreign"})
    monkeypatch.setattr(postgres_runner_cleanup, "process_groups_stopped", lambda: True)
    monkeypatch.setattr(postgres_test_runner, "build_docker_env", lambda _password: {})
    monkeypatch.setattr(
        postgres_test_runner,
        "build_docker_run_argv",
        lambda owner: captured_owner.append(owner) or ["docker", "run"],
    )

    def recorded_command(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output = "" if argv[1:3] == ["ps", "-aq"] else container_id
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(postgres_test_runner, "run_command", recorded_command)
    monkeypatch.setattr(postgres_runner_cleanup, "run_command", recorded_command)
    monkeypatch.setattr(
        postgres_test_runner,
        "inspect_identity",
        lambda identifier: (identifier, captured_owner[0].owner_token),
    )
    monkeypatch.setattr(postgres_test_runner, "inspect_storage", lambda _id, _token: (True, True))
    monkeypatch.setattr(
        postgres_test_runner, "mapped_port", lambda _id: signal.raise_signal(signal.SIGINT)
    )
    monkeypatch.setattr(postgres_runner_cleanup, "cleanup_container", lambda _owner: True)
    monkeypatch.setattr(
        postgres_runner_cleanup, "verify_container_absence", lambda _owner: (True, True, True)
    )
    monkeypatch.setattr(postgres_runner_cleanup, "docker_ids", lambda: {"foreign"})

    def remove_root(path: Path, _device: int, _inode: int) -> bool:
        shutil.rmtree(path)
        return True

    monkeypatch.setattr(postgres_runner_cleanup, "cleanup_run_root", remove_root)
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, on_signal)
    try:
        # When the runner handles a real parent SIGINT at that lifecycle boundary.
        outcome = _run_scenario("all", process_id=999999, process_identity="d" * 64)
    finally:
        signal.signal(signal.SIGINT, previous)

    # Then it preserves acquired identity and proves cleanup without inventing later observations.
    assert outcome["status"] == "interrupted"
    assert outcome["child_exit_code"] == 130
    assert outcome["container_id"] == container_id
    assert (
        outcome["container_id_sha256"]
        == "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"
    )
    assert outcome["storage_inspected"] is True
    assert outcome["port_mapping_observed"] is False
    assert outcome["warning_scan_complete"] is False
    assert outcome["cleanup_container_removed"] is True
    assert outcome["owned_label_absent"] is True
    assert outcome["port_mapping_removed"] is True
    assert outcome["process_group_stopped"] is True
    assert outcome["cleanup_run_root_removed"] is True
    assert outcome["foreign_containers_preserved"] is True
    assert not Path(str(outcome["run_root"])).exists()


def test_cleanup_failure_overrides_child_success(monkeypatch) -> None:
    # Given a successful child receipt with a captured before-snapshot.
    monkeypatch.setattr(postgres_test_runner, "start_process_scope", lambda: None)
    monkeypatch.setattr(
        postgres_test_runner,
        "_initial_outcome",
        lambda *_args, **_kwargs: {
            "scenario": "success",
            "status": "passed",
            "child_exit_code": 0,
        },
    )
    monkeypatch.setattr(postgres_test_runner, "docker_ids", lambda: {"foreign"})
    monkeypatch.setattr(postgres_test_runner, "build_docker_env", lambda _password: {})
    monkeypatch.setattr(
        postgres_test_runner,
        "run_command",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, "", ""),
    )
    monkeypatch.setattr(
        postgres_test_runner,
        "cleanup_scenario_resources",
        lambda _context: {
            "cleanup_container_removed": True,
            "owned_label_absent": True,
            "port_mapping_removed": True,
            "process_group_stopped": False,
            "cleanup_run_root_removed": True,
            "foreign_containers_observed": True,
            "foreign_containers_preserved": True,
        },
    )

    # When the scenario records its cleanup receipt.
    outcome = _run_scenario("success", process_id=999999, process_identity="d" * 64)

    # Then cleanup failure cannot be represented as a passed scenario.
    assert outcome["process_group_stopped"] is False
    assert outcome["status"] == "failed"
    assert outcome["failure_reason"] == "cleanup_failed"


def test_second_signal_during_cleanup_is_deferred_until_all_cleanup_finishes(monkeypatch) -> None:
    # Given an interrupted scenario that receives SIGTERM while removing its container.
    cleanup_steps: list[str] = []
    monkeypatch.setattr(postgres_test_runner, "start_process_scope", lambda: None)
    monkeypatch.setattr(postgres_test_runner, "docker_ids", lambda: {"foreign"})
    monkeypatch.setattr(postgres_runner_cleanup, "docker_ids", lambda: {"foreign"})
    monkeypatch.setattr(postgres_test_runner, "build_docker_env", lambda _password: {})

    commands = 0

    def interrupt_before_create(argv: list[str], **_kwargs: object):
        nonlocal commands
        commands += 1
        if commands == 1:
            signal.raise_signal(signal.SIGINT)
            return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def signal_while_cleaning(_owner: OwnedContainer) -> bool:
        cleanup_steps.append("container")
        signal.raise_signal(signal.SIGTERM)
        return True

    monkeypatch.setattr(postgres_test_runner, "run_command", interrupt_before_create)
    monkeypatch.setattr(postgres_runner_cleanup, "cleanup_container", signal_while_cleaning)
    monkeypatch.setattr(
        postgres_runner_cleanup,
        "verify_container_absence",
        lambda _owner: cleanup_steps.append("verify") or (True, True, True),
    )
    monkeypatch.setattr(
        postgres_runner_cleanup,
        "run_command",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    monkeypatch.setattr(postgres_runner_cleanup, "process_groups_stopped", lambda: True)

    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        # When both signals cross the scenario and cleanup boundaries.
        outcome = _run_scenario("all", process_id=999999, process_identity="d" * 64)
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)

    # Then cleanup completes and the stronger deferred signal determines the interrupt exit.
    assert cleanup_steps == ["container", "verify"]
    assert outcome["status"] == "interrupted"
    assert outcome["child_exit_code"] == 143
