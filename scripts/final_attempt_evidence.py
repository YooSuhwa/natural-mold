"""Evidence inventory, export, and failure-receipt validation."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from e2e_cleanup_checker import validate_payload
from e2e_cleanup_export import validate_export
from final_attempt_io import (
    SHA64,
    LifecycleError,
    UnsafeFinalAttemptEntry,
    _digest_bytes,
    _digest_json,
    _locked_parent,
    _read_json,
    _read_optional,
    _read_regular,
    validate_trusted_regular,
)
from final_attempt_review_contract import E2E_KEYS, F2_CHILD, PRESEAL_FIXED
from operation_ledger_format import JSONValue, LedgerError
from operation_ledger_fs import open_parent
from operation_ledger_writer import current_evidence_lock
from postgres_cleanup_checker import ManifestValidationError

TERMINAL_FAILURES: Final = {
    "abandon": {"f2-failure.json", "f3-failure.json"},
    "reopen": {"f1-review.md", "f4-review.md"},
}
PRESEAL_FILES: Final = frozenset(PRESEAL_FIXED)


def validate_inventory_directory(metadata: os.stat_result) -> None:
    """Require an owned directory that cannot be replaced by group/other users."""
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise UnsafeFinalAttemptEntry("inventory contains an unsafe directory")


def same_directory_identity(left: os.stat_result, right: os.stat_result) -> bool:
    """Compare both identity and trust-relevant directory metadata."""
    return (
        stat.S_ISDIR(left.st_mode)
        and stat.S_ISDIR(right.st_mode)
        and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
        and left.st_uid == right.st_uid
        and stat.S_IMODE(left.st_mode) == stat.S_IMODE(right.st_mode)
    )


def validate_inventory_file(metadata: os.stat_result) -> None:
    """Apply the control-file trust policy to retained evidence files."""
    if metadata.st_nlink != 1:
        raise UnsafeFinalAttemptEntry("inventory contains a hard-linked file")
    validate_trusted_regular(metadata, label="inventory file")


def _inventory(root: Path, *, excluded: frozenset[str] = frozenset()) -> dict[str, JSONValue]:
    try:
        active_lock = current_evidence_lock()
        anchor = root.absolute() / ".inventory-anchor"
        if active_lock is not None and anchor.is_relative_to(active_lock.evidence_root):
            root_descriptor, _ = _locked_parent(active_lock, anchor, create=False)
        else:
            root_descriptor, _ = open_parent(anchor, create=False)
    except (OSError, LedgerError) as error:
        raise LifecycleError("inventory root must be a trusted directory") from error
    files: list[dict[str, JSONValue]] = []
    try:
        opened_root = os.fstat(root_descriptor)
        named_root = os.stat(root, follow_symlinks=False)  # noqa: PTH116 - identity check
        _validate_inventory_directory(opened_root)
        if not _same_directory_identity(opened_root, named_root):
            raise LifecycleError("inventory root identity changed before descent")
        _inventory_directory(root_descriptor, "", files, excluded)
        named_root = os.stat(root, follow_symlinks=False)  # noqa: PTH116 - identity check
        _validate_inventory_directory(named_root)
        if not _same_directory_identity(opened_root, named_root):
            raise LifecycleError("inventory root identity changed after descent")
    except OSError as error:
        raise LifecycleError("inventory traversal failed") from error
    finally:
        os.close(root_descriptor)
    files.sort(key=lambda item: str(item["path"]))
    return {"files": files, "sha256": _digest_json({"files": files})}


def _validate_inventory_directory(metadata: os.stat_result) -> None:
    try:
        validate_inventory_directory(metadata)
    except UnsafeFinalAttemptEntry as error:
        raise LifecycleError(str(error)) from error


def _same_directory_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return same_directory_identity(left, right)


def _inventory_directory(
    descriptor: int,
    prefix: str,
    files: list[dict[str, JSONValue]],
    excluded: frozenset[str],
) -> None:
    for name in sorted(os.listdir(descriptor)):  # noqa: PTH208 - descriptor-bound enumeration
        before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        relative = prefix + name
        if stat.S_ISDIR(before.st_mode):
            _validate_inventory_directory(before)
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
            try:
                opened = os.fstat(child)
                _validate_inventory_directory(opened)
                if not _same_directory_identity(before, opened):
                    raise LifecycleError("inventory directory identity changed before descent")
                _inventory_directory(child, relative + "/", files, excluded)
                after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                _validate_inventory_directory(after)
                if not _same_directory_identity(opened, after):
                    raise LifecycleError("inventory directory identity changed after descent")
            finally:
                os.close(child)
            continue
        if not stat.S_ISREG(before.st_mode):
            raise LifecycleError("inventory contains a symbolic link or special entry")
        if relative in excluded:
            try:
                validate_inventory_file(before)
            except UnsafeFinalAttemptEntry as error:
                raise LifecycleError(str(error)) from error
            continue
        file_descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=descriptor,
        )
        try:
            metadata = os.fstat(file_descriptor)
            try:
                validate_inventory_file(metadata)
            except UnsafeFinalAttemptEntry as error:
                raise LifecycleError(str(error)) from error
            if (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise LifecycleError("inventory file identity changed before read")
            payload_parts: list[bytes] = []
            size = 0
            while chunk := os.read(file_descriptor, 1024 * 1024):
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise LifecycleError("inventory file exceeds the size bound")
                payload_parts.append(chunk)
            named = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            try:
                validate_inventory_file(named)
            except UnsafeFinalAttemptEntry as error:
                raise LifecycleError(str(error)) from error
            if (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise LifecycleError("inventory file identity changed")
            payload = b"".join(payload_parts)
        finally:
            os.close(file_descriptor)
        files.append(
            {
                "path": relative,
                "sha256": _digest_bytes(payload),
                "size": metadata.st_size,
            }
        )


def _validate_inventory(root: Path, expected: JSONValue | None) -> None:
    if not isinstance(expected, dict) or _inventory(root) != expected:
        raise LifecycleError("immutable attempt/export inventory changed")


def _validate_transition_inventory(
    root: Path, expected: JSONValue | None, *, excluded: frozenset[str] = frozenset()
) -> None:
    if not isinstance(expected, dict) or _inventory(root, excluded=excluded) != expected:
        raise LifecycleError("immutable attempt/export inventory changed in lifecycle phase")


def _validate_preseal_files(attempt_dir: Path) -> None:
    _validate_flat_attempt_directory(attempt_dir)
    inventory = _inventory(attempt_dir)
    files = inventory.get("files")
    if not isinstance(files, list):
        raise LifecycleError("seal prerequisite inventory is invalid")
    names = {str(item.get("path")) for item in files if isinstance(item, dict)}
    if not PRESEAL_FILES.issubset(names):
        missing = sorted(PRESEAL_FILES - names)
        raise LifecycleError(f"seal prerequisites are incomplete: {', '.join(missing)}")
    extras = names - PRESEAL_FILES
    if any(F2_CHILD.fullmatch(name) is None for name in extras):
        raise LifecycleError("seal attempt contains an unknown preseal artifact")
    for name in sorted(PRESEAL_FILES):
        if not _read_regular(attempt_dir / name):
            raise LifecycleError(f"seal prerequisite is empty: {name}")


def _validate_flat_attempt_directory(attempt_dir: Path) -> None:
    """Reject every nested attempt entry through the bound attempt descriptor."""
    try:
        active_lock = current_evidence_lock()
        anchor = attempt_dir.absolute() / ".flat-anchor"
        if active_lock is not None and anchor.is_relative_to(active_lock.evidence_root):
            descriptor, _ = _locked_parent(active_lock, anchor, create=False)
        else:
            descriptor, _ = open_parent(anchor, create=False)
    except (OSError, LedgerError) as error:
        raise LifecycleError("attempt directory cannot be descriptor-bound") from error
    try:
        for name in os.listdir(descriptor):  # noqa: PTH208 - descriptor-bound enumeration
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                raise LifecycleError("seal attempt contains a nested directory")
    except OSError as error:
        raise LifecycleError("attempt directory enumeration failed") from error
    finally:
        os.close(descriptor)


def _bind_export_directory(repo_root: Path, raw: str, expected_name: str) -> Path:
    directory = Path(raw)
    expected = repo_root.absolute() / "output" / "e2e-captures" / expected_name
    if not directory.is_absolute() or directory.absolute() != expected:
        raise LifecycleError("E2E export is outside the final-attempt namespace")
    try:
        descriptor, _ = open_parent(directory / ".inventory-anchor", create=False)
    except (OSError, LedgerError) as error:
        raise LifecycleError("E2E export directory cannot be descriptor-bound") from error
    try:
        metadata = os.fstat(descriptor)
        named = os.stat(directory, follow_symlinks=False)  # noqa: PTH116 - no-follow identity check
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise LifecycleError("E2E export directory identity is unsafe")
    finally:
        os.close(descriptor)
    return directory


def _validate_export_tree(directory: Path, export: Mapping[str, JSONValue]) -> str:
    required = {
        "schema_version",
        "secret_scan_passed",
        "failure_code",
        "export_directory",
        "attempt_id",
        "export_directory_absolute",
        "export_tree_sha256",
        "manifest",
        "files",
        "screenshots",
        "screenshots_absolute",
        "source_rejection",
    }
    if set(export) != required or export.get("schema_version") != 1:
        raise LifecycleError("final-attempt E2E export schema is invalid")
    files = export.get("files")
    if not isinstance(files, list) or not files:
        raise LifecycleError("final-attempt E2E export file inventory is invalid")
    normalized: list[dict[str, JSONValue]] = []
    for raw in files:
        if not isinstance(raw, dict) or set(raw) != {"path", "sha256", "size_bytes"}:
            raise LifecycleError("final-attempt E2E export file schema is invalid")
        relative = raw.get("path")
        digest = raw.get("sha256")
        size = raw.get("size_bytes")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(digest, str)
            or SHA64.fullmatch(digest) is None
            or type(size) is not int
            or size < 0
        ):
            raise LifecycleError("final-attempt E2E export file values are invalid")
        payload = _read_regular(directory / relative)
        if len(payload) != size or _digest_bytes(payload) != digest:
            raise LifecycleError("final-attempt E2E export file inventory changed")
        normalized.append({"path": relative, "sha256": digest, "size_bytes": size})
    independent = _inventory(directory).get("files")
    if not isinstance(independent, list):
        raise LifecycleError("final-attempt E2E export inventory is invalid")
    complete: list[dict[str, JSONValue]] = []
    for entry in independent:
        if not isinstance(entry, dict):
            raise LifecycleError("final-attempt E2E export inventory is invalid")
        complete.append(
            {
                "path": str(entry.get("path")),
                "sha256": str(entry.get("sha256")),
                "size_bytes": int(entry.get("size", -1)),
            }
        )
    complete.sort(key=lambda item: (item["path"] != "export-manifest.json", str(item["path"])))
    if normalized != complete or len({str(item["path"]) for item in normalized}) != len(normalized):
        raise LifecycleError("final-attempt E2E receipt omits or duplicates retained files")
    tree_hash = _digest_bytes(json.dumps(complete, separators=(",", ":")).encode())
    if export.get("export_tree_sha256") != tree_hash:
        raise LifecycleError("final-attempt E2E export tree hash is invalid")
    return tree_hash


def _external_exports(repo_root: Path, attempt_dir: Path, attempt_id: str) -> list[JSONValue]:
    exports: list[JSONValue] = []
    active_lock = current_evidence_lock()
    if active_lock is None:
        receipt_paths = sorted(attempt_dir.glob("*.json"))
    else:
        descriptor, _ = _locked_parent(
            active_lock, attempt_dir.absolute() / ".receipt-anchor", create=False
        )
        try:
            receipt_paths = [
                attempt_dir / name
                for name in sorted(os.listdir(descriptor))  # noqa: PTH208 - bound dirfd
                if name.endswith(".json")
            ]
        finally:
            os.close(descriptor)
    aggregate = _read_optional(attempt_dir / "f2-static.json")
    nodes = aggregate.get("nodes") if aggregate is not None else None
    expected_names = {"f3-scripted.json", "f3-capture.json", "f3-live.json"}
    if isinstance(nodes, list):
        for item in nodes:
            if not isinstance(item, dict) or not isinstance(item.get("receipt"), dict):
                continue
            relative = item["receipt"].get("relative_path")
            if isinstance(relative, str) and F2_CHILD.fullmatch(Path(relative).name):
                expected_names.add(Path(relative).name)
    for receipt_path in receipt_paths:
        if receipt_path.name not in expected_names:
            continue
        receipt = _read_json(receipt_path)
        if receipt.get("runner") != "moldy-isolated-e2e":
            continue
        absolute = receipt.get("export_directory_absolute")
        tree_hash = receipt.get("export_tree_sha256")
        project = receipt.get("project")
        suffix = {
            "scripted-full": "scripted",
            "scripted-capture": "capture",
            "live-manual": "live",
        }.get(project)
        if (
            receipt.get("attempt_id") != attempt_id
            or not isinstance(absolute, str)
            or suffix is None
            or not isinstance(tree_hash, str)
            or SHA64.fullmatch(tree_hash) is None
        ):
            raise LifecycleError("E2E export is outside the final-attempt namespace")
        if set(receipt) != E2E_KEYS:
            raise LifecycleError("final-attempt E2E receipt schema is invalid")
        child_match = F2_CHILD.fullmatch(receipt_path.name)
        export_suffix = f"f2-{child_match.group(2)}" if child_match is not None else suffix
        expected_name = (
            f"{Path(absolute).name[:8]}-runtime-policy-final-{attempt_id}-{export_suffix}"
        )
        if re.fullmatch(r"[0-9]{8}", Path(absolute).name[:8]) is None:
            raise LifecycleError("E2E export is outside the final-attempt namespace")
        directory = _bind_export_directory(repo_root, absolute, expected_name)
        export = receipt.get("export")
        if (
            not isinstance(export, dict)
            or export.get("attempt_id") != attempt_id
            or export.get("export_directory_absolute") != absolute
            or export.get("screenshots_absolute") != receipt.get("screenshots")
            or _validate_export_tree(directory, export) != tree_hash
        ):
            raise LifecycleError("E2E export hashes disagree with the run receipt")
        try:
            validate_export(export, str(project), repo_root)
            validate_payload(receipt, repository_root=repo_root, receipt_path=receipt_path)
        except (ManifestValidationError, OSError, ValueError) as error:
            raise LifecycleError("final-attempt E2E export failed canonical validation") from error
        exports.append(
            {
                "receipt_path": receipt_path.name,
                "receipt_sha256": _digest_bytes(_read_regular(receipt_path)),
                "export_directory_absolute": absolute,
                "receipt_tree_sha256": tree_hash,
                "inventory": _inventory(directory),
            }
        )
    return exports


def _failure_receipt(path: Path, attempt_dir: Path, attempt_id: str, kind: str, head: str) -> str:
    if path.parent.absolute() != attempt_dir.absolute() or path.name not in TERMINAL_FAILURES[kind]:
        raise LifecycleError(f"{kind} requires an attempt-local terminal failure receipt")
    if path.suffix == ".json":
        receipt = _read_json(path)
        gate = path.name[:2].upper()
        variant_keys = {
            "f2-failure.json": {
                "schema_version",
                "producer",
                "gate",
                "terminal",
                "attempt_id",
                "head",
                "failing_nodes",
                "artifact_hashes",
            },
            "f3-failure.json": {
                "schema_version",
                "producer",
                "gate",
                "terminal",
                "attempt_id",
                "head",
                "failing_check",
                "export_hashes",
            },
        }
        if (
            set(receipt) != variant_keys.get(path.name)
            or receipt.get("schema_version") != 1
            or receipt.get("producer") != "project-gate-runner"
            or receipt.get("gate") != gate
        ):
            raise LifecycleError("JSON failure receipt schema is invalid")
        if path.name == "f2-failure.json":
            nodes = receipt.get("failing_nodes")
            hashes = receipt.get("artifact_hashes")
            if (
                not isinstance(nodes, list)
                or not nodes
                or not all(isinstance(value, str) and value for value in nodes)
                or not isinstance(hashes, dict)
                or "f2-static.json" not in hashes
                or not _valid_hash_bindings(
                    hashes,
                    attempt_dir,
                    {"f2-static.json", "f2-code-review.md", "f2-security-review.md"},
                )
            ):
                raise LifecycleError("F2 failure receipt evidence is invalid")
        else:
            if (
                not isinstance(receipt.get("failing_check"), str)
                or not receipt.get("failing_check")
                or not isinstance(receipt.get("export_hashes"), dict)
                or not set(receipt["export_hashes"]).intersection(
                    {"f3-scripted.json", "f3-capture.json", "f3-live.json"}
                )
                or not _valid_hash_bindings(
                    receipt.get("export_hashes"),
                    attempt_dir,
                    {
                        "f3-scripted.json",
                        "f3-capture.json",
                        "f3-live.json",
                        "f3-manual-qa.md",
                    },
                )
            ):
                raise LifecycleError("F3 failure receipt evidence is invalid")
        terminal = receipt.get("terminal")
        receipt_attempt = receipt.get("attempt_id")
        receipt_head = receipt.get("head")
    else:
        try:
            content = _read_regular(path).decode("utf-8")
        except (LifecycleError, UnicodeDecodeError) as error:
            raise LifecycleError("failure receipt cannot be read") from error
        lines = content.rstrip("\n").splitlines()
        if len(lines) < 10:
            raise LifecycleError("Markdown failure receipt is incomplete")
        body = "\n".join(lines[:-9]).rstrip() + "\n"
        footer_lines = lines[-9:]
        prefixes = (
            "Receipt-Version: ",
            "Producer: ",
            "Gate: ",
            "Terminal: ",
            "Attempt-ID: ",
            "HEAD: ",
            "Artifact: ",
            "Artifact-SHA256: ",
            "Findings-SHA256: ",
        )
        if not body.strip() or any(
            not line.startswith(prefix) for line, prefix in zip(footer_lines, prefixes, strict=True)
        ):
            raise LifecycleError("Markdown failure receipt terminal metadata is invalid")
        footer = {
            "version": footer_lines[0].removeprefix(prefixes[0]),
            "producer": footer_lines[1].removeprefix(prefixes[1]),
            "gate": footer_lines[2].removeprefix(prefixes[2]),
            "terminal": footer_lines[3].removeprefix(prefixes[3]),
            "attempt_id": footer_lines[4].removeprefix(prefixes[4]),
            "head": footer_lines[5].removeprefix(prefixes[5]),
            "artifact": footer_lines[6].removeprefix(prefixes[6]),
            "artifact_sha256": footer_lines[7].removeprefix(prefixes[7]),
            "findings_sha256": footer_lines[8].removeprefix(prefixes[8]),
        }
        expected_artifact = "f1-history.json" if path.name == "f1-review.md" else "f4-scope.json"
        artifact_path = attempt_dir / expected_artifact
        if (
            footer["version"] != "1"
            or footer["producer"] != "gate-review"
            or footer["gate"] != path.name[:2].upper()
            or footer["artifact"] != expected_artifact
            or footer["artifact_sha256"] != _digest_bytes(_read_regular(artifact_path))
            or footer["findings_sha256"] != _digest_bytes(body.encode())
        ):
            raise LifecycleError("Markdown failure receipt binding is invalid")
        terminal = footer.get("terminal")
        receipt_attempt = footer.get("attempt_id")
        receipt_head = footer.get("head")
    if terminal != "FAIL" or receipt_attempt != attempt_id or receipt_head != head:
        raise LifecycleError("failure receipt is not terminal or is bound to another attempt/HEAD")
    return _digest_bytes(_read_regular(path))


def _valid_hash_bindings(
    value: JSONValue | None,
    attempt_dir: Path,
    allowed_names: set[str],
) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    for name, binding in value.items():
        if (
            not isinstance(name, str)
            or name not in allowed_names
            or Path(name).name != name
            or not isinstance(binding, dict)
            or set(binding) != {"size_bytes", "sha256"}
            or not isinstance(binding.get("size_bytes"), int)
            or binding["size_bytes"] < 0
            or not isinstance(binding.get("sha256"), str)
            or SHA64.fullmatch(binding["sha256"]) is None
        ):
            return False
        try:
            content = _read_regular(attempt_dir / name)
            if len(content) != binding["size_bytes"] or _digest_bytes(content) != binding["sha256"]:
                return False
        except LifecycleError:
            return False
    return True
