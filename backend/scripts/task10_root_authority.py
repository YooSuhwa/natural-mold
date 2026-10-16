"""Read-only validation for the Task 10 protected Root authority chain."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from scripts.task10_root_artifact_io import (
    AuthorityArtifact,
    JsonValue,
    adapt_legacy_tuple,
    artifact_tuple,
    canonical_json_bytes,
    read_regular,
)
from scripts.task10_root_authority_contract import STAGE_SPECS
from scripts.task10_root_authority_validation import (
    AuthorityValidationError as AuthorityError,
)
from scripts.task10_root_authority_validation import (
    exact,
    require,
    select_occurrence,
    strict_integer,
    valid_git_xy,
    validate_chain_step,
    validate_record,
)

CONTROL = ".omo/evidence/company-administration/task10-root-authority"
HEX40 = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class AuthorityChain:
    root: Path
    records: tuple[Mapping[str, JsonValue], ...]
    seals: tuple[Mapping[str, JsonValue], ...]
    active_seal: AuthorityArtifact


def _json(root: Path, relative: str) -> dict[str, JsonValue]:
    try:
        raw = read_regular(root, relative)
    except OSError as exc:
        raise AuthorityError("authority_artifact_unavailable") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthorityError("authority_json_invalid") from exc
    if not isinstance(value, dict):
        raise AuthorityError("authority_json_not_canonical")
    require(raw == canonical_json_bytes(value), "authority_json_not_canonical")
    return value


def parse_git_status(raw: bytes, head: str) -> dict[str, JsonValue]:
    require(HEX40.fullmatch(head) is not None, "git_status_invalid")
    require(not raw or raw.endswith(b"\0"), "git_status_invalid")
    fields, entries, paths = raw.split(b"\0"), [], []
    index = 0
    while index < len(fields) - 1:
        row = fields[index]
        require(len(row) >= 4 and row[2:3] == b" ", "git_status_invalid")
        try:
            xy = row[:2].decode("ascii")
        except UnicodeDecodeError as exc:
            raise AuthorityError("git_status_invalid") from exc
        require(valid_git_xy(xy), "git_status_invalid")
        names = [row[3:].decode("utf-8", "surrogateescape")]
        index += 1
        if any(character in "RC" for character in xy):
            require(index < len(fields) - 1, "git_rename_origin_missing")
            require(bool(fields[index]), "git_status_invalid")
            names.append(fields[index].decode("utf-8", "surrogateescape"))
            index += 1
        entries.append({"xy": xy, "paths": names})
        paths.extend(names)
    return {"head": head, "entries": entries, "canonical_paths": sorted(set(paths))}


def _git(root: Path, command: Literal["head", "status"]) -> bytes:
    match command:
        case "head":
            argv = ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"]
        case "status":
            argv = [
                "/usr/bin/git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ]
        case unreachable:
            assert_never(unreachable)
    try:
        return subprocess.run(  # noqa: S603 - fixed absolute executable and closed argv
            argv, check=True, capture_output=True
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise AuthorityError("git_read_failed") from exc


def capture_git_state(root: Path) -> dict[str, JsonValue]:
    head, status = _git(root, "head"), _git(root, "status")
    return parse_git_status(status, head.removesuffix(b"\n").decode("ascii"))


def _tuple(root: Path, value: JsonValue, content: str) -> AuthorityArtifact:
    keys = {"path", "sha256", "bytes", "content_type"}
    if not isinstance(value, dict) or set(value) != keys:
        raise AuthorityError("authority_tuple_schema")
    path, digest = value.get("path"), value.get("sha256")
    size, kind = value.get("bytes"), value.get("content_type")
    if not isinstance(path, str):
        raise AuthorityError("authority_tuple_type")
    require(isinstance(digest, str) and len(digest) == 64, "authority_tuple_type")
    require(
        isinstance(size, int) and not isinstance(size, bool) and size >= 0, "authority_tuple_type"
    )
    require(kind == content, "authority_tuple_type")
    expected = "source" if content == "source" else "metadata"
    try:
        actual = artifact_tuple(root, path, expected)
    except (OSError, ValueError) as exc:
        raise AuthorityError("authority_tuple_unavailable") from exc
    require(actual == value, "authority_tuple_changed")
    return actual


def _record(
    root: Path,
    record: dict[str, JsonValue],
    seal: dict[str, JsonValue],
    record_tuple: AuthorityArtifact,
    parent: AuthorityArtifact | None,
    sequence: int,
) -> None:
    stage = validate_record(record, seal, record_tuple, parent, sequence, str(root))
    spec = STAGE_SPECS[stage]
    candidate = record.get("candidate")
    if candidate is not None:
        _tuple(root, candidate, "metadata")
    roles = record.get("roles")
    if not isinstance(roles, list):
        raise AuthorityError("role_inventory_invalid")
    for row, expected in zip(roles, spec.roles, strict=True):
        if isinstance(row, dict):
            _tuple(root, row.get("artifact"), expected.content_type)
    publisher = record.get("publisher")
    if not isinstance(publisher, dict):
        raise AuthorityError("publisher_invalid")
    _tuple(root, publisher.get("provenance"), "metadata")


def validate_authority(
    root: Path, seal: Path | None = None, require_stage: str | None = None
) -> AuthorityChain:
    actual = root.resolve()
    locator = _json(actual, f"{CONTROL}/locator.json")
    keys = {"schema_version", "kind", "workspace_root", "generation", "active_stage"}
    keys |= {"active_anchor", "active_seal", "prior_anchor"}
    exact(locator, keys, "task10-root-authority-locator")
    generation = locator.get("generation")
    require(locator.get("workspace_root") == str(actual), "locator_generation")
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise AuthorityError("locator_generation")
    require(generation >= 1, "locator_generation")
    anchor_dir = actual / CONTROL / "anchors"
    expected = {f"{number:020d}.json" for number in range(1, generation + 1)}
    require({entry.name for entry in anchor_dir.iterdir()} == expected, "anchor_inventory_invalid")
    records: list[dict[str, JsonValue]] = []
    seals: list[dict[str, JsonValue]] = []
    prior_anchor: AuthorityArtifact | None = None
    prior_seal: AuthorityArtifact | None = None
    for sequence in range(1, generation + 1):
        anchor_path = f"{CONTROL}/anchors/{sequence:020d}.json"
        anchor = _json(actual, anchor_path)
        fields = {"schema_version", "kind", "sequence", "stage", "record", "seal", "prior_anchor"}
        exact(anchor, fields, "task10-root-authority-anchor")
        require(strict_integer(anchor.get("sequence"), sequence), "anchor_chain_invalid")
        require(anchor.get("prior_anchor") == prior_anchor, "anchor_chain_invalid")
        record_tuple = _tuple(actual, anchor.get("record"), "metadata")
        seal_tuple = _tuple(actual, anchor.get("seal"), "metadata")
        record = _json(actual, record_tuple["path"])
        sealed = _json(actual, seal_tuple["path"])
        _record(actual, record, sealed, record_tuple, prior_seal, sequence)
        validate_chain_step(record, records[-1] if records else None, sequence)
        require(anchor.get("stage") == record.get("stage"), "anchor_stage_invalid")
        records.append(record)
        seals.append(sealed)
        prior_anchor = artifact_tuple(actual, anchor_path, "metadata")
        prior_seal = seal_tuple
    previous_path = f"{CONTROL}/anchors/{generation - 1:020d}.json"
    previous = artifact_tuple(actual, previous_path, "metadata") if generation > 1 else None
    registered = (
        locator.get("active_anchor"),
        locator.get("prior_anchor"),
        locator.get("active_seal"),
        locator.get("active_stage"),
    )
    expected_locator = (prior_anchor, previous, prior_seal, records[-1].get("stage"))
    require(registered == expected_locator, "locator_not_maximal")
    if prior_seal is None:
        raise AuthorityError("authority_seal_missing")
    if seal is not None:
        require(
            seal.resolve() == (actual / prior_seal["path"]).resolve(), "authority_seal_not_active"
        )
    if require_stage is not None:
        require(records[-1].get("stage") == require_stage, "authority_stage_mismatch")
    return AuthorityChain(actual, tuple(records), tuple(seals), prior_seal)


def resolve_role(chain: AuthorityChain, stage: str, role: str) -> AuthorityArtifact:
    require(stage in STAGE_SPECS, "authority_stage_unknown")
    content = {spec.name: spec.content_type for spec in STAGE_SPECS[stage].roles}.get(role)
    record = select_occurrence(chain.records, stage)
    rows = record.get("roles")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("role") == role and content is not None:
                return _tuple(chain.root, row.get("artifact"), content)
    raise AuthorityError("root_role_not_registered")


def resolve_occurrence(
    chain: AuthorityChain,
    stage: str,
    candidate: Mapping[str, JsonValue] | None = None,
    parent_seal: Mapping[str, JsonValue] | None = None,
) -> Mapping[str, JsonValue]:
    return select_occurrence(chain.records, stage, candidate, parent_seal)


def resolve_active_scope(chain: AuthorityChain) -> Mapping[str, JsonValue]:
    return select_occurrence(chain.records, "I1_AUTHORITY_CAPACITY_AMENDED")


def validate_live_git(chain: AuthorityChain) -> None:
    require(
        chain.records[-1].get("git_state") == capture_git_state(chain.root), "live_git_state_drift"
    )


def adapt_registered_legacy(
    chain: AuthorityChain, legacy: Mapping[str, JsonValue], stage: str, role: str
) -> AuthorityArtifact:
    registered = resolve_role(chain, stage, role)
    return adapt_legacy_tuple(chain.root, legacy, registered["content_type"], registered)
