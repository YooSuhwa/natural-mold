"""Strict F2/F3 prerequisite parsing for the final-attempt seal."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from e2e_cleanup_checker import validate_payload
from e2e_cleanup_contract import LIVE_NODES
from e2e_runner_contract import FINAL_CAPTURE_SPECS
from e2e_runner_manifest import FINAL_E2E_TOP_KEYS
from final_attempt_io import (
    SHA40,
    SHA64,
    LifecycleError,
    _digest_bytes,
    _digest_json,
    _read_json,
    _read_regular,
)
from operation_ledger_format import JSONValue
from postgres_cleanup_checker import ManifestValidationError
from project_gate_catalog import CATALOG, FINAL_STATIC
from project_gate_receipts import validate_e2e, validate_postgres, validate_static
from project_gate_runtime import ProjectGateError

PRESEAL_FIXED: Final[tuple[str, ...]] = (
    "f2-static.json",
    "f2-code-review.md",
    "f2-security-review.md",
    "f3-scripted.json",
    "f3-capture.json",
    "f3-live.json",
    "f3-manual-qa.md",
)
F2_CHILD: Final = re.compile(r"f2-static\.([a-z][a-z0-9-]{0,63})\.([0-9a-f]{16})\.json\Z")
AGGREGATE_KEYS: Final = {
    "schema_version",
    "runner",
    "profile",
    "base_sha",
    "head_sha",
    "attempt_id",
    "status",
    "failure_reason",
    "runtime",
    "expected_node_ids",
    "executed_node_ids",
    "nodes",
    "cleanup_passed",
    "secret_scan_passed",
}
NODE_KEYS: Final = {"node_id", "status", "exit_code", "receipt"}
SUMMARY_KEYS: Final = {
    "relative_path",
    "sha256",
    "cleanup_passed",
    "secret_scan_passed",
    "workers",
    "retries",
    "screenshot_count",
}
E2E_KEYS: Final = FINAL_E2E_TOP_KEYS
F2_REVIEW_HEADINGS: Final = (
    "Commands and Node Counts",
    "Skips, Deselections, and Retries",
    "Migration Head",
    "Attack Matrix",
    "Cleanup",
    "Findings",
    "Verdict",
)


def _relative_child(repo_root: Path, attempt_dir: Path, value: JSONValue | None) -> Path:
    if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
        raise LifecycleError("F2 child receipt path is unsafe")
    path = repo_root / value
    if path.parent.absolute() != attempt_dir.absolute() or F2_CHILD.fullmatch(path.name) is None:
        raise LifecycleError("F2 child receipt is not an exact direct child")
    return path


def _validate_child(
    repo_root: Path,
    attempt_dir: Path,
    attempt_id: str,
    head: str,
    node_id: str,
    raw: Mapping[str, JSONValue],
) -> tuple[Path, dict[str, JSONValue]]:
    if set(raw) != NODE_KEYS or raw.get("node_id") != node_id:
        raise LifecycleError("F2 aggregate node schema is invalid")
    if raw.get("status") != "passed" or raw.get("exit_code") != 0:
        raise LifecycleError("F2 aggregate contains a failed node")
    summary = raw.get("receipt")
    if not isinstance(summary, dict) or set(summary) != SUMMARY_KEYS:
        raise LifecycleError("F2 aggregate child summary is invalid")
    path = _relative_child(repo_root, attempt_dir, summary.get("relative_path"))
    match = F2_CHILD.fullmatch(path.name)
    if match is None or match.group(1) != node_id:
        raise LifecycleError("F2 child filename disagrees with its catalog node")
    node = CATALOG[node_id]
    try:
        match node.kind:
            case "isolated":
                validated = validate_static(path, repo_root, 0)
            case "postgres":
                validated = validate_postgres(path, repo_root, "+".join(node.argv), 0)
            case "e2e":
                validated = validate_e2e(
                    path,
                    repo_root,
                    0,
                    project=node.argv[0],
                    expected_spec=node.argv[1:] or None,
                    expected_screenshot_count=node.expected_screenshot_count,
                    expected_attempt_id=attempt_id,
                    expected_head_sha=head,
                )
                payload = _read_json(path)
                if set(payload) != E2E_KEYS or payload.get("skipped_ids") != []:
                    raise LifecycleError("F2 E2E child final schema is invalid")
                validate_payload(payload, repository_root=repo_root, receipt_path=path)
            case _:
                raise LifecycleError("F2 catalog node kind is unsupported")
    except (ProjectGateError, ManifestValidationError, OSError, ValueError) as error:
        raise LifecycleError("F2 child receipt failed independent validation") from error
    if dict(validated) != dict(summary):
        raise LifecycleError("F2 aggregate child summary is not reproducible")
    return path, payload if node.kind == "e2e" else {}


def validate_f2(
    repo_root: Path, attempt_dir: Path, attempt_id: str, head: str, base_sha: str
) -> tuple[dict[str, JSONValue], tuple[Path, ...]]:
    """Validate the aggregate, all catalog children, and both machine-bound reviews."""
    aggregate_path = attempt_dir / "f2-static.json"
    aggregate = _read_json(aggregate_path)
    runtime = aggregate.get("runtime")
    expected = list(FINAL_STATIC)
    if (
        SHA64.fullmatch(attempt_id) is None
        or SHA40.fullmatch(head) is None
        or SHA40.fullmatch(base_sha) is None
        or set(aggregate) != AGGREGATE_KEYS
        or aggregate.get("schema_version") != 1
        or aggregate.get("runner") != "moldy-composite-project-gate"
        or aggregate.get("profile") != "final-static"
        or aggregate.get("base_sha") != base_sha
        or aggregate.get("head_sha") != head
        or aggregate.get("attempt_id") != attempt_id
        or aggregate.get("status") != "passed"
        or aggregate.get("failure_reason") is not None
        or not isinstance(runtime, dict)
        or set(runtime) != {"python", "node", "pnpm"}
        or not all(isinstance(value, str) and value for value in runtime.values())
        or aggregate.get("expected_node_ids") != expected
        or aggregate.get("executed_node_ids") != expected
        or aggregate.get("cleanup_passed") is not True
        or aggregate.get("secret_scan_passed") is not True
    ):
        raise LifecycleError("F2 aggregate contract is invalid")
    nodes = aggregate.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != len(expected):
        raise LifecycleError("F2 aggregate node count is invalid")
    child_paths: list[Path] = []
    for node_id, raw in zip(expected, nodes, strict=True):
        if not isinstance(raw, dict):
            raise LifecycleError("F2 aggregate node schema is invalid")
        child, _payload = _validate_child(repo_root, attempt_dir, attempt_id, head, node_id, raw)
        child_paths.append(child)
    if len({path.name for path in child_paths}) != len(child_paths):
        raise LifecycleError("F2 aggregate reuses a child receipt")
    aggregate_sha = _digest_bytes(_read_regular(aggregate_path))
    _validate_f2_review(attempt_dir / "f2-code-review.md", "code", attempt_id, head, aggregate_sha)
    _validate_f2_review(
        attempt_dir / "f2-security-review.md",
        "security",
        attempt_id,
        head,
        aggregate_sha,
    )
    return aggregate, tuple(child_paths)


def _footer(content: str, prefixes: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    lines = content.rstrip("\n").splitlines()
    if len(lines) <= len(prefixes):
        raise LifecycleError("review receipt is incomplete")
    body_lines = lines[: -len(prefixes)]
    footer_lines = lines[-len(prefixes) :]
    if any(
        not line.startswith(prefix) for line, prefix in zip(footer_lines, prefixes, strict=True)
    ):
        raise LifecycleError("review footer schema is invalid")
    values = {
        prefix.removesuffix(": "): line.removeprefix(prefix)
        for prefix, line in zip(prefixes, footer_lines, strict=True)
    }
    return "\n".join(body_lines).rstrip() + "\n", values


def _validate_f2_review(
    path: Path, role: str, attempt_id: str, head: str, aggregate_sha: str
) -> None:
    try:
        content = _read_regular(path).decode("utf-8")
    except UnicodeDecodeError as error:
        raise LifecycleError("F2 review is not UTF-8") from error
    prefixes = (
        "Receipt-Version: ",
        "Producer: ",
        "Role: ",
        "Gate: ",
        "Terminal: ",
        "Attempt-ID: ",
        "HEAD: ",
        "Artifact: ",
        "Artifact-SHA256: ",
        "Body-SHA256: ",
    )
    body, footer = _footer(content, prefixes)
    required_heading = f"# F2 {role.title()} Review\n"
    cursor = body.removeprefix(required_heading)
    complete = body.startswith(required_heading)
    for heading in F2_REVIEW_HEADINGS:
        marker = f"\n## {heading}\n"
        before, found, after = cursor.partition(marker)
        complete = (
            complete and bool(found) and (not before.strip() or heading != F2_REVIEW_HEADINGS[0])
        )
        if found and heading != F2_REVIEW_HEADINGS[0]:
            complete = complete and bool(before.strip())
        cursor = after
    complete = complete and bool(cursor.strip())
    if not complete:
        raise LifecycleError("F2 review body is incomplete")
    if footer != {
        "Receipt-Version": "1",
        "Producer": f"final-{role}-review",
        "Role": role,
        "Gate": "F2",
        "Terminal": "APPROVE",
        "Attempt-ID": attempt_id,
        "HEAD": head,
        "Artifact": "f2-static.json",
        "Artifact-SHA256": aggregate_sha,
        "Body-SHA256": _digest_bytes(body.encode()),
    }:
        raise LifecycleError("F2 review footer binding is invalid")


def _validate_final_e2e(
    repo_root: Path, path: Path, attempt_id: str, head: str
) -> dict[str, JSONValue]:
    expected = {
        "f3-scripted.json": ("scripted", "scripted-full", [], 0),
        "f3-capture.json": (
            "scripted",
            "scripted-capture",
            list(FINAL_CAPTURE_SPECS),
            24,
        ),
        "f3-live.json": ("live", "live-manual", [], 0),
    }[path.name]
    lane, project, specs, screenshot_count = expected
    payload = _read_json(path)
    screenshots = payload.get("screenshots")
    selected = payload.get("selected_ids")
    if (
        set(payload) != E2E_KEYS
        or payload.get("lane") != lane
        or payload.get("project") != project
        or payload.get("attempt_id") != attempt_id
        or payload.get("head_sha") != head
        or payload.get("requested_specs") != specs
        or payload.get("skipped_ids") != []
        or payload.get("status") != "passed"
        or payload.get("failure_reason") is not None
        or payload.get("child_exit_code") != 0
        or payload.get("self_test") != "normal"
        or payload.get("workers") != 1
        or payload.get("retries") != 0
        or payload.get("reuse_existing_server") is not False
        or not isinstance(selected, list)
        or not selected
        or not all(isinstance(item, str) for item in selected)
        or not isinstance(payload.get("executed_ids"), list)
        or not all(isinstance(item, str) for item in payload["executed_ids"])
        or selected != payload.get("executed_ids")
        or len(selected) != len({str(item) for item in selected})
        or payload.get("unexpected_failures") != []
        or not isinstance(screenshots, list)
        or len(screenshots) != screenshot_count
        or (project == "live-manual" and selected != list(LIVE_NODES))
    ):
        raise LifecycleError("F3 final receipt contract is invalid")
    try:
        validate_payload(payload, repository_root=repo_root, receipt_path=path)
    except (ManifestValidationError, OSError, ValueError) as error:
        raise LifecycleError("F3 final receipt failed canonical validation") from error
    return payload


def validate_f3(
    repo_root: Path, attempt_dir: Path, attempt_id: str, head: str
) -> tuple[dict[str, JSONValue], dict[str, JSONValue], dict[str, JSONValue]]:
    """Validate the three final-only E2E receipts and manual QA attestation."""
    receipts = tuple(
        _validate_final_e2e(repo_root, attempt_dir / name, attempt_id, head)
        for name in ("f3-scripted.json", "f3-capture.json", "f3-live.json")
    )
    _validate_manual_qa(attempt_dir, attempt_id, head, receipts[1])
    return receipts


def _validate_manual_qa(
    attempt_dir: Path, attempt_id: str, head: str, capture: Mapping[str, JSONValue]
) -> None:
    try:
        content = _read_regular(attempt_dir / "f3-manual-qa.md").decode("utf-8")
    except UnicodeDecodeError as error:
        raise LifecycleError("F3 manual QA is not UTF-8") from error
    prefixes = (
        "Receipt-Version: ",
        "Producer: ",
        "Role: ",
        "Gate: ",
        "Terminal: ",
        "Attempt-ID: ",
        "HEAD: ",
        "Scripted-SHA256: ",
        "Capture-SHA256: ",
        "Live-SHA256: ",
        "Screenshot-Inventory-SHA256: ",
        "File-Probe-Count: ",
        "View-Count: ",
        "Visual: ",
        "Redaction: ",
        "Residue: ",
        "Body-SHA256: ",
    )
    body, footer = _footer(content, prefixes)
    findings = body.split("\n## Findings\n", 1)
    visual = findings[1].split("\n## Visual Review\n", 1) if len(findings) == 2 else []
    if (
        not body.startswith("# F3 Manual QA\n")
        or len(visual) != 2
        or not visual[0].strip()
        or not visual[1].strip()
    ):
        raise LifecycleError("F3 manual QA body is incomplete")
    export = capture.get("export")
    screenshot_list = export.get("screenshots") if isinstance(export, dict) else None
    export_files = export.get("files") if isinstance(export, dict) else None
    screenshot_inventory = (
        [
            item
            for item in export_files
            if isinstance(item, dict) and item.get("path") in screenshot_list
        ]
        if isinstance(export_files, list) and isinstance(screenshot_list, list)
        else []
    )
    if (
        not isinstance(screenshot_list, list)
        or len(screenshot_list) != 24
        or len(screenshot_inventory) != 24
        or [item.get("path") for item in screenshot_inventory] != screenshot_list
    ):
        raise LifecycleError("F3 capture screenshot inventory is invalid")
    expected = {
        "Receipt-Version": "1",
        "Producer": "final-manual-qa",
        "Role": "manual-qa",
        "Gate": "F3",
        "Terminal": "APPROVE",
        "Attempt-ID": attempt_id,
        "HEAD": head,
        "Scripted-SHA256": _digest_bytes(_read_regular(attempt_dir / "f3-scripted.json")),
        "Capture-SHA256": _digest_bytes(_read_regular(attempt_dir / "f3-capture.json")),
        "Live-SHA256": _digest_bytes(_read_regular(attempt_dir / "f3-live.json")),
        "Screenshot-Inventory-SHA256": _digest_json({"screenshots": screenshot_inventory}),
        "File-Probe-Count": "24",
        "View-Count": "24",
        "Visual": "PASS",
        "Redaction": "PASS",
        "Residue": "PASS",
        "Body-SHA256": _digest_bytes(body.encode()),
    }
    if footer != expected:
        raise LifecycleError("F3 manual QA footer binding is invalid")


def prerequisite_attestation(
    repo_root: Path,
    attempt_dir: Path,
    attempt_id: str,
    head: str,
    base_sha: str,
    inventory: Mapping[str, JSONValue],
    external_exports: list[JSONValue],
) -> dict[str, JSONValue]:
    """Build the canonical seal prerequisite attestation from independently validated files."""
    _aggregate, child_paths = validate_f2(repo_root, attempt_dir, attempt_id, head, base_sha)
    validate_f3(repo_root, attempt_dir, attempt_id, head)
    paths = [attempt_dir / name for name in PRESEAL_FIXED] + list(child_paths)
    files = inventory.get("files")
    expected_paths = [path.name for path in paths]
    actual_paths = (
        [str(item.get("path")) for item in files if isinstance(item, dict)]
        if isinstance(files, list)
        else []
    )
    if sorted(actual_paths) != sorted(expected_paths) or len(actual_paths) != len(expected_paths):
        raise LifecycleError("seal attempt inventory is not the exact prerequisite set")
    expected_export_receipts = {
        "f3-scripted.json",
        "f3-capture.json",
        "f3-live.json",
        *(
            path.name
            for path in child_paths
            if _read_json(path).get("runner") == "moldy-isolated-e2e"
        ),
    }
    export_receipts = [
        str(item.get("receipt_path")) for item in external_exports if isinstance(item, dict)
    ]
    export_directories = [
        str(item.get("export_directory_absolute"))
        for item in external_exports
        if isinstance(item, dict)
    ]
    if (
        set(export_receipts) != expected_export_receipts
        or len(export_receipts) != len(expected_export_receipts)
        or len(export_directories) != len(set(export_directories))
    ):
        raise LifecycleError("seal external export set is not exact and unique")
    receipts: list[JSONValue] = []
    for path in paths:
        payload = _read_regular(path)
        receipts.append(
            {
                "path": path.name,
                "sha256": _digest_bytes(payload),
                "size_bytes": len(payload),
                "node_id": (match.group(1) if (match := F2_CHILD.fullmatch(path.name)) else None),
            }
        )
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "head": head,
        "base_sha": base_sha,
        "receipts": receipts,
        "attempt_inventory": dict(inventory),
        "external_exports": external_exports,
    }


__all__ = ["PRESEAL_FIXED", "prerequisite_attestation", "validate_f2", "validate_f3"]
