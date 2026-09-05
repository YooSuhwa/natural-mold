"""Focused final-attempt producer adapter contracts."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import postgres_manifest_io  # noqa: E402
import project_gate_composite  # noqa: E402
from e2e_runner_contract import (  # noqa: E402
    FINAL_CAPTURE_SPECS,
    E2eContractError,
    FinalE2eRun,
    Lane,
    Project,
    SelfTest,
    parse_final_e2e_run,
)
from e2e_runner_manifest import FINAL_E2E_EXPORT_KEYS, FINAL_E2E_TOP_KEYS  # noqa: E402
from operation_ledger_format import canonical_line  # noqa: E402
from operation_ledger_writer import append_operation  # noqa: E402
from plan_history_support import HEAD, _begin, lifecycle_arguments  # noqa: E402
from postgres_manifest_io import (  # noqa: E402
    ManifestPathError,
    close_evidence_directory,
    open_manifest_directory,
    write_manifest,
)
from project_gate_catalog import CATALOG  # noqa: E402
from project_gate_manifest import AggregateWriter  # noqa: E402
from project_gate_process import command_for  # noqa: E402
from project_gate_receipts import E2E_CLEANUP_KEYS, validate_e2e  # noqa: E402
from project_gate_toolchain import (  # noqa: E402
    ExecutableIdentity,
    RepositoryProvenance,
    TrustedToolchain,
)


def _attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str, str]:
    repo = tmp_path / "repo"
    lifecycle = lifecycle_arguments(repo)
    pointer = _begin(lifecycle)
    operations = lifecycle["operations"]
    assert isinstance(operations, Path)
    evidence = operations.parent
    attempt_id = str(pointer["attempt_id"])
    head = HEAD
    monkeypatch.setattr(postgres_manifest_io, "_current_git_head", lambda _repo: head)
    return repo, evidence, attempt_id, head


def _forge_open_authority(evidence: Path, mutation: str) -> None:
    operations = evidence / "operations.ndjson"
    if mutation == "missing_ledger":
        operations.unlink()
        return
    if mutation == "no_active_lifecycle":
        lines = operations.read_bytes().splitlines(keepends=True)
        operations.write_bytes(b"".join(lines[:-1]))
        return
    if mutation == "sealed_lifecycle":
        append_operation(
            operations,
            task_id="final-attempt",
            action_class="seal",
            arguments={},
            status="passed",
        )
        return
    journal_path = next((evidence / "lifecycle-journals").glob("begin-*.json"))
    journal = json.loads(journal_path.read_bytes())
    journal["phase"] = "prepared"
    journal_path.write_bytes(canonical_line(journal))


def test_final_e2e_destination_binds_exact_capture_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, head = _attempt(tmp_path, monkeypatch)
    destination = evidence / "final-attempts" / attempt_id / "f3-capture.json"

    bound = open_manifest_directory(destination, evidence, repo)
    try:
        final = parse_final_e2e_run(
            destination,
            lane="scripted",
            project="scripted-capture",
            requested_specs=FINAL_CAPTURE_SPECS,
            export_slug=f"runtime-policy-final-{attempt_id}-capture",
            attempt_id=bound.attempt_id,
            head_sha=bound.head_sha,
        )
    finally:
        close_evidence_directory(bound)

    assert final is not None
    assert final.head_sha == head
    assert final.requested_specs == FINAL_CAPTURE_SPECS


def test_final_static_aggregate_is_written_through_bound_attempt_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, head = _attempt(tmp_path, monkeypatch)
    destination = evidence / "final-attempts" / attempt_id / "f2-static.json"
    writer = AggregateWriter.create(destination, repo, head)
    try:
        writer.write({"schema_version": 1, "attempt_id": attempt_id})
    finally:
        writer.close()

    assert json.loads(destination.read_text(encoding="utf-8"))["attempt_id"] == attempt_id


def test_final_e2e_destination_rejects_filename_selection_and_slug_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, _head = _attempt(tmp_path, monkeypatch)
    destination = evidence / "final-attempts" / attempt_id / "f3-scripted.json"
    bound = open_manifest_directory(destination, evidence, repo)
    try:
        with pytest.raises(E2eContractError, match="selection_mismatch"):
            parse_final_e2e_run(
                destination,
                lane="scripted",
                project="scripted-capture",
                requested_specs=FINAL_CAPTURE_SPECS,
                export_slug=f"runtime-policy-final-{attempt_id}-capture",
                attempt_id=bound.attempt_id,
                head_sha=bound.head_sha,
            )
        with pytest.raises(E2eContractError, match="slug_mismatch"):
            parse_final_e2e_run(
                destination,
                lane="scripted",
                project="scripted-full",
                requested_specs=(),
                export_slug=f"runtime-policy-final-{attempt_id}-live",
                attempt_id=bound.attempt_id,
                head_sha=bound.head_sha,
            )
    finally:
        close_evidence_directory(bound)


def test_bound_final_parent_rejects_replacement_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, _head = _attempt(tmp_path, monkeypatch)
    attempt = evidence / "final-attempts" / attempt_id
    destination = attempt / "f3-scripted.json"
    bound = open_manifest_directory(destination, evidence, repo)
    detached = attempt.with_name("detached")
    attempt.rename(detached)
    attempt.mkdir(mode=0o700)
    try:
        with pytest.raises(ManifestPathError, match="evidence_identity"):
            write_manifest(destination, {"status": "passed"}, bound)
    finally:
        close_evidence_directory(bound)

    assert not destination.exists()
    assert not (detached / destination.name).exists()


def test_bound_final_parent_rejects_replacement_during_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, _head = _attempt(tmp_path, monkeypatch)
    attempt = evidence / "final-attempts" / attempt_id
    destination = attempt / "f3-scripted.json"
    bound = open_manifest_directory(destination, evidence, repo)
    detached = attempt.with_name("detached-during-write")
    real_write = postgres_manifest_io._write_all

    def replace_parent(descriptor: int, payload: bytes) -> None:
        real_write(descriptor, payload)
        attempt.rename(detached)
        attempt.mkdir(mode=0o700)

    monkeypatch.setattr(postgres_manifest_io, "_write_all", replace_parent)
    try:
        with pytest.raises(ManifestPathError, match="evidence_identity"):
            write_manifest(destination, {"status": "passed"}, bound)
    finally:
        close_evidence_directory(bound)

    assert not destination.exists()
    assert not (detached / destination.name).exists()


def test_final_destination_rejects_closed_pointer_and_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, _head = _attempt(tmp_path, monkeypatch)
    destination = evidence / "final-attempts" / attempt_id / "f3-live.json"
    destination.write_text("occupied", encoding="utf-8")
    with pytest.raises(FileExistsError):
        open_manifest_directory(destination, evidence, repo)
    destination.unlink()
    pointer_path = evidence / "current-final-attempt.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["status"] = "sealed"
    pointer["seal_id"] = "c" * 32
    pointer["seal_sha256"] = "d" * 64
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(ManifestPathError, match="final_attempt_binding"):
        open_manifest_directory(destination, evidence, repo)


def test_ordinary_manifest_remains_a_direct_evidence_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, _attempt_id, _head = _attempt(tmp_path, monkeypatch)
    destination = evidence / "ordinary.json"
    bound = open_manifest_directory(destination, evidence, repo)
    try:
        write_manifest(destination, {"status": "passed"}, bound)
    finally:
        close_evidence_directory(bound)

    assert json.loads(destination.read_text(encoding="utf-8")) == {"status": "passed"}


@pytest.mark.parametrize(
    "mutation",
    ["missing_ledger", "no_active_lifecycle", "sealed_lifecycle", "nonterminal_journal"],
)
def test_f3_binding_rejects_forged_open_pointer_without_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    repo, evidence, attempt_id, _head = _attempt(tmp_path, monkeypatch)
    destination = evidence / "final-attempts" / attempt_id / "f3-scripted.json"
    _forge_open_authority(evidence, mutation)

    with pytest.raises(ManifestPathError, match="final_attempt_binding"):
        open_manifest_directory(destination, evidence, repo)

    assert not destination.exists()


@pytest.mark.parametrize(
    "mutation",
    ["missing_ledger", "no_active_lifecycle", "sealed_lifecycle", "nonterminal_journal"],
)
def test_f2_producer_rejects_forged_open_pointer_without_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    repo, evidence, attempt_id, head = _attempt(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location(
        f"isolated_manifest_authority_{mutation}", REPO_ROOT / "scripts/isolated-manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: type("GitResult", (), {"returncode": 0, "stdout": f"{head}\n"})(),
    )
    token = "c" * 16
    node = "final-backend-ruff"
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_ID", attempt_id)
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_HEAD", head)
    monkeypatch.setenv("MOLDY_FINAL_NODE_ID", node)
    monkeypatch.setenv("MOLDY_FINAL_RECEIPT_TOKEN", token)
    destination = evidence / "final-attempts" / attempt_id / f"f2-static.{node}.{token}.json"
    _forge_open_authority(evidence, mutation)

    with pytest.raises(OSError, match="stale"):
        module.prepare_manifest(destination)

    assert not destination.exists()


def test_f2_e2e_child_uses_collision_free_attempt_slug(tmp_path: Path) -> None:
    attempt_id = "a" * 64
    token = "c" * 16
    node = CATALOG["todo21-runtime-policy-e2e"]
    receipt = tmp_path / f"f2-static.{node.node_id}.{token}.json"
    toolchain = TrustedToolchain(
        Path("/python"),
        Path("/node"),
        Path("/pnpm"),
        Path("/uv"),
        tmp_path,
        "tester",
    )

    command, environment = command_for(
        node,
        receipt,
        tmp_path,
        toolchain,
        attempt_id=attempt_id,
        head_sha="b" * 40,
    )

    assert command[-4:] == list(node.argv[1:])
    assert environment["E2E_EXPORT_SLUG"] == (f"runtime-policy-final-{attempt_id}-f2-{token}")
    assert len(f"20260905-{environment['E2E_EXPORT_SLUG']}") <= 128


def test_static_f2_writer_accepts_only_bound_attempt_child_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, head = _attempt(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "isolated_manifest_final_test", REPO_ROOT / "scripts/isolated-manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: type("GitResult", (), {"returncode": 0, "stdout": f"{head}\n"})(),
    )
    token = "c" * 16
    node = "final-backend-ruff"
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_ID", attempt_id)
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_HEAD", head)
    monkeypatch.setenv("MOLDY_FINAL_NODE_ID", node)
    monkeypatch.setenv("MOLDY_FINAL_RECEIPT_TOKEN", token)
    destination = evidence / "final-attempts" / attempt_id / f"f2-static.{node}.{token}.json"

    parent_identity, file_identity = module.prepare_manifest(destination)
    module.finalize_manifest(destination, parent_identity, file_identity, b'{"status":"passed"}')

    assert destination.read_bytes() == b'{"status":"passed"}'
    wrong = destination.with_name(f"f2-static.frontend-lint.{token}.json")
    with pytest.raises(OSError, match="producer boundary"):
        module.prepare_manifest(wrong)


def test_static_f2_writer_rejects_same_uid_name_swap_after_parent_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, head = _attempt(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "isolated_manifest_toctou_test", REPO_ROOT / "scripts/isolated-manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: type("GitResult", (), {"returncode": 0, "stdout": f"{head}\n"})(),
    )
    token = "c" * 16
    node = "final-backend-ruff"
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_ID", attempt_id)
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_HEAD", head)
    monkeypatch.setenv("MOLDY_FINAL_NODE_ID", node)
    monkeypatch.setenv("MOLDY_FINAL_RECEIPT_TOKEN", token)
    destination = evidence / "final-attempts" / attempt_id / f"f2-static.{node}.{token}.json"
    parent_identity, file_identity = module.prepare_manifest(destination)
    displaced = destination.with_name(f"{destination.name}.displaced")
    replacement = destination.with_name(f"{destination.name}.replacement")
    real_fsync = module.os.fsync
    swapped = False

    def swap_name_after_parent_fsync(descriptor: int) -> None:
        nonlocal swapped
        real_fsync(descriptor)
        if not swapped and module._identity(module.os.fstat(descriptor)) == parent_identity:
            destination.rename(displaced)
            replacement.write_bytes(b'{"status":"substituted"}')
            replacement.chmod(0o600)
            replacement.replace(destination)
            swapped = True

    monkeypatch.setattr(module.os, "fsync", swap_name_after_parent_fsync)
    with pytest.raises(OSError, match="identity changed"):
        module.finalize_manifest(
            destination, parent_identity, file_identity, b'{"status":"passed"}'
        )

    assert swapped is True
    assert destination.stat().st_uid == os.geteuid()
    assert destination.read_bytes() == b'{"status":"substituted"}'
    assert displaced.read_bytes() == b'{"status":"passed"}'


@pytest.mark.parametrize("mode", [0o660, 0o666])
def test_static_f2_writer_rejects_writable_reserved_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    repo, evidence, attempt_id, head = _attempt(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "isolated_manifest_mode_test", REPO_ROOT / "scripts/isolated-manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: type("GitResult", (), {"returncode": 0, "stdout": f"{head}\n"})(),
    )
    token = "c" * 16
    node = "final-backend-ruff"
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_ID", attempt_id)
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_HEAD", head)
    monkeypatch.setenv("MOLDY_FINAL_NODE_ID", node)
    monkeypatch.setenv("MOLDY_FINAL_RECEIPT_TOKEN", token)
    destination = evidence / "final-attempts" / attempt_id / f"f2-static.{node}.{token}.json"
    parent_identity, file_identity = module.prepare_manifest(destination)
    destination.chmod(mode)

    with pytest.raises(OSError, match="identity changed"):
        module.verify_manifest(destination, parent_identity, file_identity)
    with pytest.raises(OSError, match="identity changed"):
        module.finalize_manifest(
            destination, parent_identity, file_identity, b'{"status":"passed"}'
        )

    assert destination.stat().st_mode & 0o777 == mode
    assert destination.read_bytes() == b""


def _seal_pointer(evidence: Path) -> None:
    pointer_path = evidence / "current-final-attempt.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer.update(status="sealed", seal_id="c" * 32, seal_sha256="d" * 64)
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")


def test_composite_revalidates_final_binding_before_child_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, head = _attempt(tmp_path, monkeypatch)
    destination = evidence / "final-attempts" / attempt_id / "f2-static.json"
    provenance = RepositoryProvenance(base_sha=head, head_sha=head)
    docker = Path("/usr/local/bin/docker")
    docker_identity = ExecutableIdentity(docker, 1, 2, docker, 3, 4, 5, 6, 7, "a" * 64)
    toolchain = TrustedToolchain(
        Path("/python"),
        Path("/node"),
        Path("/pnpm"),
        Path("/uv"),
        repo,
        "x",
        identities=(docker_identity,),
    )
    called = False
    original = project_gate_composite.command_for

    def seal_after_reservation(*args: object, **kwargs: object) -> tuple[list[str], dict[str, str]]:
        command, environment = original(*args, **kwargs)  # type: ignore[arg-type]
        _seal_pointer(evidence)
        return command, environment

    def child(*_args: object) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(project_gate_composite, "command_for", seal_after_reservation)
    monkeypatch.setattr(project_gate_composite, "run_process", child)
    monkeypatch.setattr(project_gate_composite, "verify_toolchain", lambda _toolchain: None)
    monkeypatch.setattr(project_gate_composite, "verify_provenance", lambda *_args: None)
    with pytest.raises(Exception, match="manifest"):
        project_gate_composite.run_composite(
            "final-static",
            ("todo07-project-facts",),
            provenance,
            toolchain,
            {},
            destination,
            repo,
            {},
        )
    assert called is False


def test_e2e_cli_revalidates_final_binding_before_resource_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import e2e_runner_cli

    repo, evidence, attempt_id, _head = _attempt(tmp_path, monkeypatch)
    destination = evidence / "final-attempts" / attempt_id / "f3-scripted.json"
    called = False
    real_parse = e2e_runner_cli.parse_final_e2e_run

    def parse_then_seal(*args: object, **kwargs: object) -> object:
        result = real_parse(*args, **kwargs)  # type: ignore[arg-type]
        _seal_pointer(evidence)
        return result

    def runner(
        lane: Lane,
        project: Project,
        arguments: tuple[str, ...],
        self_test: SelfTest,
        final_run: FinalE2eRun | None,
    ) -> tuple[dict[str, object], int]:
        nonlocal called
        del lane, project, arguments, self_test, final_run
        called = True
        return {}, 0

    monkeypatch.setattr(e2e_runner_cli, "REPO_ROOT", repo)
    monkeypatch.setattr(e2e_runner_cli, "parse_final_e2e_run", parse_then_seal)
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "scripted", "--project", "scripted-full", "--manifest", str(destination)],
    )
    monkeypatch.setenv("E2E_EXPORT_SLUG", f"runtime-policy-final-{attempt_id}-scripted")
    with pytest.raises(ManifestPathError, match="final_attempt_binding"):
        e2e_runner_cli.run_cli(runner)
    assert called is False


def test_postgres_cli_revalidates_final_binding_before_scenario_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, _head = _attempt(tmp_path, monkeypatch)
    token = "c" * 16
    destination = evidence / "final-attempts" / attempt_id / f"f2-static.postgres-all.{token}.json"
    called = False
    real_validate = postgres_manifest_io._validate_final_postgres_manifest

    def validate_then_seal(*args: object, **kwargs: object) -> None:
        real_validate(*args, **kwargs)  # type: ignore[arg-type]
        _seal_pointer(evidence)

    def runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(postgres_manifest_io, "REPO_ROOT", repo)
    monkeypatch.setattr(
        postgres_manifest_io, "_validate_final_postgres_manifest", validate_then_seal
    )
    monkeypatch.setattr(sys, "argv", ["runner", "all", "--manifest", str(destination)])
    monkeypatch.setenv("E2E_EXPORT_SLUG", f"runtime-policy-final-{attempt_id}-f2-{token}")
    with pytest.raises(ManifestPathError, match="final_attempt_binding"):
        postgres_manifest_io.run_cli(runner, lambda *_args, **_kwargs: {})
    assert called is False


def test_isolated_manifest_verify_stops_spawn_after_pointer_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence, attempt_id, head = _attempt(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "isolated_manifest_verify_test", REPO_ROOT / "scripts/isolated-manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "EVIDENCE_ROOT", evidence)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: type("GitResult", (), {"returncode": 0, "stdout": f"{head}\n"})(),
    )
    token = "c" * 16
    node = "final-backend-ruff"
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_ID", attempt_id)
    monkeypatch.setenv("MOLDY_FINAL_ATTEMPT_HEAD", head)
    monkeypatch.setenv("MOLDY_FINAL_NODE_ID", node)
    monkeypatch.setenv("MOLDY_FINAL_RECEIPT_TOKEN", token)
    destination = evidence / "final-attempts" / attempt_id / f"f2-static.{node}.{token}.json"
    parent_identity, file_identity = module.prepare_manifest(destination)
    _seal_pointer(evidence)
    spawned = False
    with pytest.raises(OSError, match="stale"):
        module.verify_manifest(destination, parent_identity, file_identity)
    assert spawned is False


def _final_gate_receipt(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    attempt_id = "a" * 64
    head = "b" * 40
    node = "scripted-full::e2e/smoke.spec.ts::works"
    export: dict[str, object] = {}
    export.update((key, None) for key in FINAL_E2E_EXPORT_KEYS)
    export.update(
        schema_version=1,
        secret_scan_passed=True,
        attempt_id=attempt_id,
        export_directory="output/e2e-captures/20260905-runtime-policy-final-"
        + attempt_id
        + "-scripted",
        export_directory_absolute=str(tmp_path / "export"),
        export_tree_sha256="c" * 64,
        screenshots_absolute=[],
        screenshots=[],
    )
    payload: dict[str, object] = {}
    payload.update((key, None) for key in FINAL_E2E_TOP_KEYS)
    payload.update(
        schema_version=1,
        runner="moldy-isolated-e2e",
        lane="scripted",
        project="scripted-full",
        workers=1,
        retries=0,
        status="passed",
        child_exit_code=0,
        attempt_id=attempt_id,
        head_sha=head,
        requested_specs=[],
        skipped_ids=[],
        selected_ids=[node],
        executed_ids=[node],
        unexpected_failures=[],
        export=export,
        cleanup=dict.fromkeys(E2E_CLEANUP_KEYS, True),
    )
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


@pytest.mark.parametrize(
    "mutation", ["top_key", "export_key", "malformed_skip", "duplicate_skip", "success_skip"]
)
def test_project_gate_rejects_final_receipt_schema_and_skip_mutations(
    tmp_path: Path, mutation: str
) -> None:
    path, payload = _final_gate_receipt(tmp_path)
    skipped = "scripted-full::e2e/skip.spec.ts::skipped"
    if mutation == "top_key":
        payload["unknown"] = True
    elif mutation == "export_key":
        export = payload["export"]
        assert isinstance(export, dict)
        export["unknown"] = True
    elif mutation == "malformed_skip":
        payload["skipped_ids"] = ["../escape"]
    elif mutation == "duplicate_skip":
        payload["skipped_ids"] = [skipped, skipped]
    else:
        payload["skipped_ids"] = [skipped]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Exception, match="invalid_child_receipt"):
        validate_e2e(
            path,
            tmp_path,
            0,
            project="scripted-full",
            expected_spec=None,
            expected_attempt_id="a" * 64,
            expected_head_sha="b" * 40,
        )
