"""Shared constants and builders for plan-history lifecycle tests."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from e2e_cleanup_contract import CLEANUP_FIELDS, LIVE_NODES  # noqa: E402
from e2e_runner_contract import FINAL_CAPTURE_SPECS  # noqa: E402
from final_attempt_io import _digest_json, _read_json  # noqa: E402
from final_attempt_lifecycle import begin_final_attempt, seal_attempt  # noqa: E402
from operation_ledger_format import JSONValue, LedgerError, canonical_line  # noqa: E402
from operation_ledger_writer import _active_attempt_from_lifecycle, append_operation  # noqa: E402
from plan_history_contract import (  # noqa: E402
    PRIMARY_TRAILER,
    CommitRecord,
    load_contract,
    load_verified_operations,
    read_git_history,
)
from project_gate_catalog import CATALOG, FINAL_STATIC  # noqa: E402
from project_gate_receipts import validate_e2e, validate_postgres, validate_static  # noqa: E402

CONTRACT_PATH = SCRIPTS / "project-restart-plan-contract.json"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "project_restart_plan_history"
# The roadmap evidence directory is intentionally ignored: it is live, mutable
# operator output rather than a test dependency.  Lifecycle and history tests
# need a reviewed, immutable snapshot instead so they remain reproducible in a
# clean checkout and cannot race the active evidence writer.
EVIDENCE = FIXTURE_ROOT
PLAN_SHA = "4b86a0acc57c1309ca3f84b0726f09c31ea1893fbc0789dd4c2c09e12b46846e"
REVIEW_ROUND = "review-20260830T155022Z-4b86a0ac"
# The fixture ledger ends at this reviewed commit.  Later maintenance commits
# are intentionally not part of the immutable plan-history fixture and do not
# need roadmap trailers to exercise the historical contract.
FIXTURE_HISTORY_HEAD = "87b0d1490b4bf5bbced34680298dd176948566d7"
HEAD = "f" * 40


def copy_isolated_operations(destination: Path) -> None:
    """Copy a verified ledger, excluding an unmatched source open attempt."""
    source = EVIDENCE / "operations.ndjson"
    entries = load_verified_operations(source)
    source_bytes = source.read_bytes()
    lines = source_bytes.splitlines(keepends=True)
    if len(lines) != len(entries):
        raise LedgerError("verified source ledger line count changed during fixture copy")

    copied = source_bytes
    pointer_path = EVIDENCE / "current-final-attempt.json"
    if pointer_path.exists():
        pointer = _read_json(pointer_path)
        if pointer.get("status") == "open":
            attempt_id = pointer.get("attempt_id")
            head = pointer.get("head")
            if not isinstance(attempt_id, str) or not isinstance(head, str):
                raise LedgerError("source open final-attempt pointer is invalid")
            expected_pointer: dict[str, JSONValue] = {
                "schema_version": 1,
                "attempt_id": attempt_id,
                "attempt_dir": (
                    ".omo/evidence/project-restart-consolidated-roadmap/final-attempts/"
                    f"{attempt_id}"
                ),
                "status": "open",
                "head": head,
            }
            if pointer != expected_pointer:
                raise LedgerError("source open final-attempt pointer schema is invalid")
            if _active_attempt_from_lifecycle(entries) != attempt_id:
                raise LedgerError("source open pointer does not match the active lifecycle attempt")
            matches = [
                index
                for index, entry in enumerate(entries)
                if entry.get("action_class") == "final_attempt_started"
                and entry.get("status") == "passed"
                and entry.get("arguments") == {"attempt_id": attempt_id, "head": head}
            ]
            if len(matches) != 1:
                raise LedgerError("source open final attempt must have one matching ledger entry")
            copied = b"".join(lines[: matches[0]])

    destination.write_bytes(copied)
    copied_entries = load_verified_operations(destination)
    if copied_entries != entries[: len(copied_entries)]:
        raise LedgerError("isolated ledger copy is not a verified source prefix")


def lifecycle_arguments(tmp_path: Path) -> dict[str, object]:
    """Create one isolated lifecycle layout backed by a verified ledger copy."""
    evidence = tmp_path / ".omo/evidence/project-restart-consolidated-roadmap"
    evidence.mkdir(parents=True)
    operations = evidence / "operations.ndjson"
    copy_isolated_operations(operations)
    return {
        "repo_root": tmp_path,
        "contract": load_contract(CONTRACT_PATH),
        "plan_sha": PLAN_SHA,
        "review_round": REVIEW_ROUND,
        "operations": operations,
        "head": HEAD,
        "attempt_root": evidence / "final-attempts",
        "pointer_path": evidence / "current-final-attempt.json",
        "journal_root": evidence / "lifecycle-journals",
    }


def _cli_path(path: Path) -> str:
    return os.path.relpath(path, Path.cwd())


def _write_json(path: Path, value: dict[str, JSONValue]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_line(value))
    return path


def _write_final_e2e_receipt(
    repo_root: Path,
    attempt_dir: Path,
    attempt_id: str,
    *,
    export_parent: Path | None = None,
) -> Path:
    return _write_e2e_receipt(
        repo_root,
        attempt_dir / "f3-scripted.json",
        attempt_id,
        HEAD,
        "scripted-full",
        (),
        "scripted",
        0,
        export_parent=export_parent,
    )


def _write_e2e_receipt(
    repo_root: Path,
    path: Path,
    attempt_id: str,
    head: str,
    project: str,
    specs: tuple[str, ...],
    suffix: str,
    screenshot_count: int,
    *,
    export_parent: Path | None = None,
) -> Path:
    parent = export_parent or repo_root / "output/e2e-captures"
    export_dir = parent / f"20260905-runtime-policy-final-{attempt_id}-{suffix}"
    export_dir.mkdir(parents=True, exist_ok=True)
    (repo_root / ".gitignore").write_text("output/\n", encoding="utf-8")
    screenshot_paths = [f"captures/screenshot-{index:02d}.png" for index in range(screenshot_count)]
    for index, relative in enumerate(screenshot_paths):
        screenshot = export_dir / relative
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]))
    artifact_files: list[dict[str, JSONValue]] = []
    for relative in screenshot_paths:
        content = (export_dir / relative).read_bytes()
        artifact_files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    exporter_manifest: dict[str, JSONValue] = {
        "schema_version": 1,
        "project": project,
        "policy": {
            "version": 1,
            "screenshots": "scripted-capture-only",
            "max_file_bytes": 20 * 1024 * 1024,
            "max_total_bytes": 50 * 1024 * 1024,
        },
        "secret_scan": {"passed": True, "exact_secret_count": 0},
        "files": artifact_files,
        "total": {
            "file_count": len(artifact_files),
            "size_bytes": sum(int(item["size_bytes"]) for item in artifact_files),
        },
    }
    content = (json.dumps(exporter_manifest, sort_keys=True) + "\n").encode()
    (export_dir / "export-manifest.json").write_bytes(content)
    files: list[dict[str, JSONValue]] = [
        {
            "path": "export-manifest.json",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
    ] + artifact_files
    tree_hash = hashlib.sha256(json.dumps(files, separators=(",", ":")).encode()).hexdigest()
    export: dict[str, JSONValue] = {
        "schema_version": 1,
        "secret_scan_passed": True,
        "failure_code": None,
        "export_directory": export_dir.relative_to(repo_root).as_posix()
        if export_parent is None
        else f"output/e2e-captures/{export_dir.name}",
        "attempt_id": attempt_id,
        "export_directory_absolute": str(export_dir),
        "export_tree_sha256": tree_hash,
        "manifest": files[0],
        "files": files,
        "screenshots": screenshot_paths,
        "screenshots_absolute": [str(export_dir / item) for item in screenshot_paths],
        "source_rejection": None,
    }
    receipt: dict[str, JSONValue] = {
        "schema_version": 1,
        "runner": "moldy-isolated-e2e",
        "lane": "live" if project == "live-manual" else "scripted",
        "project": project,
        "workers": 1,
        "retries": 0,
        "reuse_existing_server": False,
        "status": "passed",
        "failure_reason": None,
        "child_exit_code": 0,
        "self_test": "normal",
        "run_id": "a" * 24,
        "head_sha": head,
        "requested_specs": list(specs),
        "skipped_ids": [],
        "attempt_id": attempt_id,
        "export_directory_absolute": str(export_dir),
        "export_tree_sha256": tree_hash,
        "screenshots": [str(export_dir / item) for item in screenshot_paths],
        "owned_run_root": True,
        "owned_database": True,
        "owned_backend": True,
        "owned_frontend": True,
        "owned_proxy": project == "live-manual",
        "postgres_image": "postgres:16-alpine",
        "server_version_num": "160001",
        "alembic_head": "m70",
        "alembic_current": "m70",
        "schema_fingerprint": "b" * 64,
        "second_upgrade_idempotent": True,
        "frontend_port": 3200 if project == "live-manual" else 3100,
        "backend_port": 8201 if project == "live-manual" else 8101,
        "selected_ids": list(LIVE_NODES)
        if project == "live-manual"
        else [f"{project}::{spec}::passes" for spec in (specs or ("e2e/smoke.spec.ts",))],
        "executed_ids": list(LIVE_NODES)
        if project == "live-manual"
        else [f"{project}::{spec}::passes" for spec in (specs or ("e2e/smoke.spec.ts",))],
        "unexpected_failures": [],
        "export": export,
        "egress": {
            "enabled": True,
            "clean_stop": True,
            "records": [
                {
                    "method": "POST",
                    "origin": "https://llm.invalid",
                    "path_class": "chat_completions",
                    "status": 200,
                    "count": 4,
                }
            ],
        }
        if project == "live-manual"
        else {"enabled": False, "clean_stop": True, "records": []},
        "cleanup": {**dict.fromkeys(CLEANUP_FIELDS, True), "foreign_containers_preserved": True},
    }
    return _write_json(path, receipt)


def _write_failure_receipt(
    attempt_dir: Path, attempt_id: str, gate: str, *, terminal: str = "FAIL"
) -> Path:
    if gate == "F2":
        artifact = _write_json(attempt_dir / "f2-static.json", {"status": "FAIL"})
        artifact_content = artifact.read_bytes()
        evidence: dict[str, JSONValue] = {
            "failing_nodes": ["backend::focused-gate"],
            "artifact_hashes": {
                artifact.name: {
                    "size_bytes": len(artifact_content),
                    "sha256": hashlib.sha256(artifact_content).hexdigest(),
                }
            },
        }
    else:
        artifact = _write_final_e2e_receipt(attempt_dir.parents[4], attempt_dir, attempt_id)
        artifact_content = artifact.read_bytes()
        evidence = {
            "failing_check": "scripted-full::artifact-contract",
            "export_hashes": {
                artifact.name: {
                    "size_bytes": len(artifact_content),
                    "sha256": hashlib.sha256(artifact_content).hexdigest(),
                }
            },
        }
    return _write_json(
        attempt_dir / f"{gate.lower()}-failure.json",
        {
            "schema_version": 1,
            "producer": "project-gate-runner",
            "gate": gate,
            "terminal": terminal,
            "attempt_id": attempt_id,
            "head": HEAD,
            **evidence,
        },
    )


def _write_review_receipt(attempt_dir: Path, attempt_id: str, gate: str) -> Path:
    artifact_name = "f1-history.json" if gate == "F1" else "f4-scope.json"
    artifact = _write_json(attempt_dir / artifact_name, {"status": "FAIL"})
    artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    body = f"# {gate} review\n\n## Findings\n\nBlocking gate finding.\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    content = (
        body
        + f"Receipt-Version: 1\nProducer: gate-review\nGate: {gate}\nTerminal: FAIL\n"
        + f"Attempt-ID: {attempt_id}\nHEAD: {HEAD}\nArtifact: {artifact_name}\n"
        + f"Artifact-SHA256: {artifact_digest}\nFindings-SHA256: {digest}\n"
    )
    path = attempt_dir / f"{gate.lower()}-review.md"
    path.write_text(content, encoding="utf-8")
    return path


def _prepare_seal_prerequisites(attempt_dir: Path) -> None:
    fixed = {
        "f2-static.json",
        "f2-code-review.md",
        "f2-security-review.md",
        "f3-scripted.json",
        "f3-capture.json",
        "f3-live.json",
        "f3-manual-qa.md",
    }
    if all((attempt_dir / name).is_file() for name in fixed) and len(
        list(attempt_dir.glob("f2-static.*.*.json"))
    ) == len(FINAL_STATIC):
        return
    repo_root = attempt_dir.parents[4]
    attempt_id = attempt_dir.name
    contract = load_contract(CONTRACT_PATH)
    summaries: list[dict[str, JSONValue]] = []
    for node_id in FINAL_STATIC:
        node = CATALOG[node_id]
        token = hashlib.sha256(node_id.encode()).hexdigest()[:16]
        child = attempt_dir / f"f2-static.{node_id}.{token}.json"
        if node.kind == "isolated":
            _write_json(
                child,
                {
                    "schema_version": 1,
                    "status": "passed",
                    "child_exit_code": 0,
                    "cleanup": "removed",
                    "run_root_sha256": "a" * 64,
                },
            )
            summary = validate_static(child, repo_root, 0)
        elif node.kind == "postgres":
            mode = "+".join(node.argv)
            scenario: dict[str, JSONValue] = {
                "scenario": mode,
                "child_exit_code": 0,
                "cleanup_container_removed": True,
                "owned_label_absent": True,
                "port_mapping_removed": True,
                "process_group_stopped": True,
                "cleanup_run_root_removed": True,
            }
            if mode == "migration-roundtrip":
                scenario.update({"test_receipt": None, "migration_roundtrip": True})
            else:
                scenario["test_receipt"] = {
                    "selected_node_ids": [f"{mode}::node"],
                    "executed_node_ids": [f"{mode}::node"],
                    "failed_node_ids": [],
                    "skipped_node_ids": [],
                    "deselected_node_ids": [],
                }
            _write_json(
                child,
                {"schema_version": 1, "mode": mode, "status": "passed", "scenarios": [scenario]},
            )
            summary = validate_postgres(child, repo_root, mode, 0)
        else:
            project = node.argv[0]
            specs = node.argv[1:]
            _write_e2e_receipt(
                repo_root,
                child,
                attempt_id,
                HEAD,
                project,
                specs,
                f"f2-{token}",
                node.expected_screenshot_count or 0,
            )
            summary = validate_e2e(
                child,
                repo_root,
                0,
                project=project,
                expected_spec=specs or None,
                expected_screenshot_count=node.expected_screenshot_count,
                expected_attempt_id=attempt_id,
                expected_head_sha=HEAD,
            )
        summaries.append(
            {"node_id": node_id, "status": "passed", "exit_code": 0, "receipt": dict(summary)}
        )
    aggregate = _write_json(
        attempt_dir / "f2-static.json",
        {
            "schema_version": 1,
            "runner": "moldy-composite-project-gate",
            "profile": "final-static",
            "base_sha": contract.base_sha,
            "head_sha": HEAD,
            "attempt_id": attempt_id,
            "status": "passed",
            "failure_reason": None,
            "runtime": {"python": "3.12.11", "node": "22.22.0", "pnpm": "10.0.0"},
            "expected_node_ids": list(FINAL_STATIC),
            "executed_node_ids": list(FINAL_STATIC),
            "nodes": summaries,
            "cleanup_passed": True,
            "secret_scan_passed": True,
        },
    )
    aggregate_sha = hashlib.sha256(aggregate.read_bytes()).hexdigest()
    for role in ("code", "security"):
        body = (
            f"# F2 {role.title()} Review\n\n"
            "## Commands and Node Counts\n\nAll catalog commands and node counts match.\n\n"
            "## Skips, Deselections, and Retries\n\nNo skips or deselections; retries are zero.\n\n"
            "## Migration Head\n\nMigration head and current revision match.\n\n"
            "## Attack Matrix\n\nThe reviewed attack matrix passes.\n\n"
            "## Cleanup\n\nAll cleanup receipts pass.\n\n"
            "## Findings\n\nNo blocking findings.\n\n"
            "## Verdict\n\nAPPROVE.\n"
        )
        content = body + (
            f"Receipt-Version: 1\nProducer: final-{role}-review\nRole: {role}\nGate: F2\n"
            f"Terminal: APPROVE\nAttempt-ID: {attempt_id}\nHEAD: {HEAD}\n"
            f"Artifact: f2-static.json\nArtifact-SHA256: {aggregate_sha}\n"
            f"Body-SHA256: {hashlib.sha256(body.encode()).hexdigest()}\n"
        )
        (attempt_dir / f"f2-{role}-review.md").write_text(content, encoding="utf-8")
    scripted = _write_e2e_receipt(
        repo_root,
        attempt_dir / "f3-scripted.json",
        attempt_id,
        HEAD,
        "scripted-full",
        (),
        "scripted",
        0,
    )
    capture = _write_e2e_receipt(
        repo_root,
        attempt_dir / "f3-capture.json",
        attempt_id,
        HEAD,
        "scripted-capture",
        FINAL_CAPTURE_SPECS,
        "capture",
        24,
    )
    live = _write_e2e_receipt(
        repo_root,
        attempt_dir / "f3-live.json",
        attempt_id,
        HEAD,
        "live-manual",
        (),
        "live",
        0,
    )
    capture_payload = json.loads(capture.read_bytes())
    screenshot_paths = capture_payload["export"]["screenshots"]
    screenshot_inventory = [
        item for item in capture_payload["export"]["files"] if item["path"] in screenshot_paths
    ]
    body = (
        "# F3 Manual QA\n\n## Findings\n\nNo blocking findings.\n\n"
        "## Visual Review\n\nAll captures reviewed.\n"
    )
    manual = body + (
        "Receipt-Version: 1\nProducer: final-manual-qa\nRole: manual-qa\nGate: F3\n"
        "Terminal: APPROVE\n"
        f"Attempt-ID: {attempt_id}\nHEAD: {HEAD}\n"
        f"Scripted-SHA256: {hashlib.sha256(scripted.read_bytes()).hexdigest()}\n"
        f"Capture-SHA256: {hashlib.sha256(capture.read_bytes()).hexdigest()}\n"
        f"Live-SHA256: {hashlib.sha256(live.read_bytes()).hexdigest()}\n"
        f"Screenshot-Inventory-SHA256: {_digest_json({'screenshots': screenshot_inventory})}\n"
        "File-Probe-Count: 24\nView-Count: 24\nVisual: PASS\nRedaction: PASS\nResidue: PASS\n"
        f"Body-SHA256: {hashlib.sha256(body.encode()).hexdigest()}\n"
    )
    (attempt_dir / "f3-manual-qa.md").write_text(manual, encoding="utf-8")


def _seal(lifecycle: dict[str, object], output: Path, hook=None) -> dict[str, JSONValue]:
    pointer = json.loads(Path(lifecycle["pointer_path"]).read_bytes())
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _prepare_seal_prerequisites(attempt_dir)
    return seal_attempt(**lifecycle, output=output, hook=hook)


def _cross_process_append(path: str, queue: multiprocessing.Queue[str]) -> None:
    queue.put("started")
    try:
        append_operation(
            Path(path),
            task_id="fixture",
            action_class="concurrent-mutation",
            arguments={},
            status="passed",
        )
    except LedgerError:
        queue.put("rejected")
    else:
        queue.put("accepted")


def _complete_history() -> tuple[object, tuple[CommitRecord, ...], list[dict[str, JSONValue]]]:
    contract = load_contract(CONTRACT_PATH)
    commits = list(read_git_history(REPO_ROOT, contract.base_sha, FIXTURE_HISTORY_HEAD))
    entries = load_verified_operations(EVIDENCE / "operations.ndjson")
    observed_primary = {
        item for commit in commits for item in PRIMARY_TRAILER.findall(commit.message)
    }
    for item in (item for item in ("23", "24", "25") if item not in observed_primary):
        digest = hashlib.sha256(f"plan-history-fixture:{item}".encode()).hexdigest()
        sha = digest[:40]
        tree_sha = hashlib.sha256(f"plan-history-fixture-tree:{item}".encode()).hexdigest()[:40]
        commits.append(
            CommitRecord(sha, (commits[-1].sha,), tree_sha, f"item\n\nPlan-Item: {item}\n")
        )
        entry: dict[str, JSONValue] = {
            "schema_version": 1,
            "sequence": len(entries),
            "previous_entry_hash": entries[-1]["entry_hash"],
            "timestamp_utc": "2026-09-05T00:00:00Z",
            "task_id": item,
            "action_class": "commit",
            "arguments": {"commit_sha": sha, "plan_item": item},
            "status": "passed",
            "entry_hash": "",
        }
        entry["entry_hash"] = hashlib.sha256(
            canonical_line({key: value for key, value in entry.items() if key != "entry_hash"})
        ).hexdigest()
        entries.append(entry)
    return contract, tuple(commits), entries


@pytest.fixture
def lifecycle(tmp_path: Path) -> dict[str, object]:
    """Provide an isolated final-attempt lifecycle layout."""
    return lifecycle_arguments(tmp_path)


def _begin(lifecycle: dict[str, object], hook=None) -> dict[str, JSONValue]:
    return begin_final_attempt(**lifecycle, hook=hook)
