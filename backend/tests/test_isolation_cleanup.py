"""Validation contracts for disposable PostgreSQL lifecycle receipts."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import postgres_manifest_io  # noqa: E402
import postgres_test_runner  # noqa: E402
from postgres_cleanup_checker import ManifestValidationError, validate_payload  # noqa: E402
from postgres_runner_runtime import ScenarioKind  # noqa: E402


def _scenario(name: str = "all") -> dict[str, object]:
    selected = ["tests/integration/test_x.py::test_x"]
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
        "child_exit_code": {"success": 0, "child_failure": 23, "sigint": 130}.get(name, 0),
        "image": "postgres:16-alpine",
        "server_version_num": "160010",
        "container_id": "a" * 64,
        "run_id": "b" * 24,
        "container_id_sha256": "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb",
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
        "test_receipt": receipt if name == "all" else None,
        "cleanup_container_removed": True,
        "owned_label_absent": True,
        "port_mapping_removed": True,
        "process_group_stopped": True,
        "cleanup_run_root_removed": True,
        "foreign_containers_observed": True,
        "foreign_containers_preserved": True,
    }


def _parent_interrupt_scenario(*, container_created: bool = True) -> dict[str, object]:
    """Build a cancellation receipt at a specific lifecycle boundary."""
    scenario: dict[str, object] = {
        "scenario": "all",
        "status": "interrupted",
        "failure_reason": "signal",
        "child_exit_code": 130,
        "image": "postgres:16-alpine",
        "run_id": "b" * 24,
        "run_root": "/tmp/.moldy-pg-run-interrupt",
        "run_root_created": True,
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
        "foreign_containers_observed": container_created,
        "foreign_containers_preserved": True if container_created else None,
    }
    if container_created:
        container_id = "a" * 64
        scenario["container_id"] = container_id
        scenario["container_id_sha256"] = hashlib.sha256(container_id.encode()).hexdigest()
    return scenario


def _parent_interrupt_payload(*, container_created: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "all",
        "status": "interrupted",
        "concurrent_pair": False,
        "scenarios": [_parent_interrupt_scenario(container_created=container_created)],
    }


@pytest.mark.parametrize("interrupt_after", [1, 2])
def test_self_test_parent_interrupt_stops_at_acquired_scenario(
    interrupt_after: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a sequential self-test interrupted in its first or second acquired scenario.
    calls: list[ScenarioKind] = []
    captured: list[dict[str, object]] = []
    dummy = postgres_manifest_io.EvidenceDirectory(tmp_path, -1, 1, 1)

    def run_scenario(
        kind: ScenarioKind, *, process_id: int, process_identity: str
    ) -> dict[str, object]:
        del process_id, process_identity
        calls.append(kind)
        if len(calls) == interrupt_after:
            interrupted = _parent_interrupt_scenario()
            interrupted["scenario"] = kind
            return interrupted
        return _scenario(kind)

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
        sys, "argv", ["postgres_test_runner.py", "self-test", "--manifest", str(tmp_path / "x")]
    )

    # When the self-test CLI handles the acquired-resource interrupt.
    exit_code = postgres_manifest_io.run_cli(
        run_scenario, postgres_test_runner._early_interrupt_outcome
    )

    # Then it stops immediately, preserves the partial receipt, and remains checker-valid.
    assert exit_code == 130
    assert calls == ["success", "child_failure"][:interrupt_after]
    assert captured[0]["status"] == "interrupted"
    assert captured[0]["concurrent_pair"] is False
    validate_payload(captured[0])


def test_validate_payload_accepts_complete_self_test_receipt() -> None:
    # Given the three fixed self-test outcomes.
    payload = {
        "schema_version": 1,
        "mode": "self-test",
        "status": "passed",
        "concurrent_pair": False,
        "scenarios": [_scenario("success"), _scenario("child_failure"), _scenario("sigint")],
    }

    # When the checker validates the immutable receipt.
    validate_payload(payload)

    # Then no exception is raised.


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (("cleanup_container_removed", False), "cleanup_container"),
        (("foreign_containers_preserved", False), "foreign_container"),
        (("foreign_containers_preserved", None), "foreign_container"),
        (("warning_hits", ["ResourceWarning"]), "resource_warning"),
        (("server_version_num", "150000"), "server_major"),
    ],
)
def test_validate_payload_rejects_failed_cleanup_or_resource_receipt(
    mutation: tuple[str, object],
    reason: str,
) -> None:
    # Given one corrupted scenario field.
    scenario = _scenario()
    scenario[mutation[0]] = mutation[1]
    payload = {
        "schema_version": 1,
        "mode": "all",
        "status": "passed",
        "concurrent_pair": False,
        "scenarios": [scenario],
    }

    # When validation runs, then a stable reason is raised.
    with pytest.raises(ManifestValidationError) as caught:
        validate_payload(payload)
    assert str(caught.value) == reason


def test_validate_payload_rejects_credential_shaped_content() -> None:
    # Given a manifest with a credential-bearing URL hidden in an extra field.
    scenario = _scenario()
    scenario["unexpected"] = "postgresql://runner:secret@127.0.0.1:5432/db"
    payload = {
        "schema_version": 1,
        "mode": "all",
        "status": "passed",
        "concurrent_pair": False,
        "scenarios": [scenario],
    }

    # When validation runs, then redaction validation fails without reflecting it.
    with pytest.raises(ManifestValidationError) as caught:
        validate_payload(payload)
    assert str(caught.value) == "secret_material"


def test_validate_payload_accepts_parent_interrupt_before_port_or_alembic() -> None:
    # Given an actual parent signal after Docker created the owned container but before inspection.
    payload = _parent_interrupt_payload()

    # When the cancellation-specific manifest path is validated.
    validate_payload(payload)

    # Then the truthful partial lifecycle receipt is accepted without a test or migration claim.


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (("container_id_sha256", "e" * 64), "container_hash"),
        (("container_id", None), "container_identity"),
        (("cleanup_container_removed", False), "cleanup_container"),
        (("owned_label_absent", False), "owned_label"),
        (("port_mapping_removed", False), "port_mapping"),
        (("process_group_stopped", False), "process_group"),
        (("cleanup_run_root_removed", False), "cleanup_run_root"),
        (("foreign_containers_preserved", False), "foreign_container"),
        (("warning_hits", ["ResourceWarning"]), "resource_warning"),
        (("tmpfs_storage", True), "tmpfs_observation"),
        (("port_mapping_observed", True), "port_observation"),
    ],
)
def test_validate_payload_rejects_tampered_parent_interrupt_cleanup_or_identity(
    mutation: tuple[str, object], reason: str
) -> None:
    # Given a cancellation receipt with one forged lifecycle observation.
    scenario = _parent_interrupt_scenario()
    scenario[mutation[0]] = mutation[1]
    payload = {
        "schema_version": 1,
        "mode": "all",
        "status": "interrupted",
        "concurrent_pair": False,
        "scenarios": [scenario],
    }

    # When the checker validates the receipt, then it exposes only the stable contract reason.
    with pytest.raises(ManifestValidationError) as caught:
        validate_payload(payload)
    assert str(caught.value) == reason


def test_validate_payload_accepts_parent_interrupt_before_docker_creation() -> None:
    # Given a parent signal before Docker returned a container identity.
    payload = _parent_interrupt_payload(container_created=False)

    # When cancellation validation runs.
    validate_payload(payload)

    # Then no unavailable Docker, port, server, Alembic, or pytest claim is required.


def test_validate_payload_rejects_unobserved_foreign_container_claim() -> None:
    # Given an early interrupt that claims preservation without a before/after snapshot.
    payload = _parent_interrupt_payload(container_created=False)
    scenarios = payload.get("scenarios")
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    scenario["foreign_containers_preserved"] = True

    # When the checker validates progressive observations, then the invented claim is rejected.
    with pytest.raises(ManifestValidationError, match="foreign_container_observation"):
        validate_payload(payload)
