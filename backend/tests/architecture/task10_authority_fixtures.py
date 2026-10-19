"""Independent stdlib-only registrar for Task 10 private authority fixtures."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tests.architecture.task10_authority_literal_catalog import BASELINE

type JsonValue = str | int | float | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]

CONTROL: Final = Path(".omo/evidence/company-administration/task10-root-authority")
SOURCE_CAPS: Final = {
    "scripts/task10_root_artifact_io.py": 180,
    "scripts/task10_root_authority.py": 240,
    "scripts/task10_root_authority_contract.py": 250,
    "scripts/task10_root_authority_validation.py": 250,
    "tests/architecture/task10_authority_fixtures.py": 240,
    "tests/architecture/task10_authority_literal_catalog.py": 220,
    "tests/architecture/task10_authority_scenarios.py": 250,
    "tests/architecture/task10_review_fixtures.py": 250,
    "tests/architecture/test_task10_root_authority.py": 250,
}
ROLE_NAMES: Final = tuple(role.name for role in BASELINE)


class FixtureRegistrationError(ValueError): ...


@dataclass(frozen=True, slots=True)
class PrivateAuthority:
    root: Path
    seal: Path
    record: Path
    locator: Path
    role_paths: dict[str, Path]


def fixture_head(authority: PrivateAuthority) -> str:
    state = json.loads(authority.record.read_text()).get("git_state")
    if not isinstance(state, dict) or not isinstance(head := state.get("head"), str):
        raise FixtureRegistrationError("fixture_head_invalid")
    return head


def fixture_output_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "out").mkdir(parents=True)
    return root


def fixture_roots(tmp_path: Path, *names: str) -> tuple[Path, ...]:
    roots = tuple(tmp_path / name for name in names)
    for root in roots:
        root.mkdir()
    return roots


def write_candidate_document(
    root: Path, prefix: str, authority: PrivateAuthority
) -> dict[str, JsonValue]:
    value = {"candidate": fixture_head(authority), "workspace_root": str(root.resolve())}
    value["source_proof"] = "private"
    relative = f"{CONTROL}/fixture-inputs/{prefix}/candidate.json"
    return write_fixture_artifact(root, relative, value)


def write_argv_roles(root: Path, prefix: str, *roles: str) -> dict[str, dict[str, JsonValue]]:
    artifacts = {}
    for role in roles:
        value: dict[str, JsonValue] = {"manifest_kind": "argv_manifest", "cwd": "backend"}
        value["argv"] = ["task10-private-fixture", role]
        relative = f"{CONTROL}/fixture-inputs/{prefix}/{role}.json"
        artifacts[role] = write_fixture_artifact(root, relative, value)
    return artifacts


def fixture_digest(artifact: Mapping[str, JsonValue]) -> str:
    value = artifact["sha256"]
    if not isinstance(value, str):
        raise FixtureRegistrationError("fixture_digest_invalid")
    return value


def legacy_artifact(root: Path, artifact: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    path, digest, size = artifact.get("path"), artifact.get("sha256"), artifact.get("bytes")
    if not isinstance(path, str) or not isinstance(digest, str) or type(size) is not int:
        raise FixtureRegistrationError("fixture_artifact_invalid")
    return {"path": str((root / path).resolve()), "sha256": digest, "bytes": size}


def _bytes(value: Mapping[str, JsonValue]) -> bytes:
    return f"{json.dumps(value, sort_keys=True, separators=(',', ':'))}\n".encode()


def _role_value(role: str) -> dict[str, JsonValue]:
    if role == "preedit_test_node_inventory":
        return {
            "manifest_kind": "test_node_inventory",
            "collection_argv": [],
            "nodes": [],
        }
    if role == "preedit_source_snapshot_manifest":
        return {
            "manifest_kind": "source_snapshot_manifest",
            "snapshot_root": "snapshots",
            "entries": [],
        }
    return {"fixture_role": role}


def _write(path: Path, value: Mapping[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_bytes(value))


def fixture_artifact(
    root: Path, path: Path, content_type: str = "metadata"
) -> dict[str, JsonValue]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "content_type": content_type,
    }


def _git(root: Path, *args: str) -> bytes:
    argv = ["/usr/bin/git", "-C", str(root), *args]
    return subprocess.run(argv, check=True, capture_output=True).stdout


def init_private_root(root: Path) -> str:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Authority Fixture")
    _git(root, "config", "user.email", "authority@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    hooks = root / ".empty-hooks"
    hooks.mkdir()
    _git(root, "config", "core.hooksPath", str(hooks))
    exclude = root / ".git/info/exclude"
    exclude.write_text(
        ".omo/evidence/company-administration/task10-root-authority/\n.empty-hooks/\n"
    )
    tracked = root / "tracked.txt"
    tracked.write_text("fixture\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-q", "-m", "fixture")
    return _git(root, "rev-parse", "HEAD").removesuffix(b"\n").decode()


def write_fixture_artifact(
    root: Path,
    relative: str,
    value: Mapping[str, JsonValue],
    content_type: str = "metadata",
) -> dict[str, JsonValue]:
    path = root / relative
    _write(path, value)
    return fixture_artifact(root, path, content_type)


def replace_private_role(
    previous: PrivateAuthority, role: str, label: str
) -> tuple[list[dict[str, JsonValue]], dict[str, Path]]:
    document = json.loads(previous.record.read_text())
    raw_roles = document.get("roles")
    if not isinstance(raw_roles, list):
        raise FixtureRegistrationError("fixture_roles_invalid")
    rows: list[dict[str, JsonValue]] = []
    paths = dict(previous.role_paths)
    for raw in raw_roles:
        if not isinstance(raw, dict):
            raise FixtureRegistrationError("fixture_roles_invalid")
        row = dict(raw)
        if row.get("role") == role:
            relative = f"{CONTROL}/fixture-inputs/{label}-{role}.json"
            row["artifact"] = write_fixture_artifact(
                previous.root, relative, {"fixture_role": role, "label": label}
            )
            paths[role] = previous.root / relative
        rows.append(row)
    return rows, paths


def append_private_stage(
    root: Path,
    stage: str,
    status: str,
    candidate: Mapping[str, JsonValue] | None,
    roles: Sequence[Mapping[str, JsonValue]],
    role_paths: Mapping[str, Path],
    previous: PrivateAuthority | None = None,
) -> PrivateAuthority:
    control = root / CONTROL
    provenance = control / "fixture-inputs/provenance.json"
    if _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise FixtureRegistrationError("private_fixture_git_dirty")
    head = _git(root, "rev-parse", "HEAD").removesuffix(b"\n").decode()
    sequence = 1
    parent_seal: dict[str, JsonValue] | None = None
    prior_anchor: JsonValue = None
    if previous is not None:
        locator = json.loads(previous.locator.read_text())
        sequence = locator["generation"] + 1
        parent_seal = fixture_artifact(root, previous.seal)
        prior_anchor = locator["active_anchor"]
    record_id = str(uuid.uuid4())
    publisher = {"actor_role": "fixture_registrar", "session_id": "private-fixture"}
    publisher |= {"provenance": fixture_artifact(root, provenance)}
    publisher |= {"output_scope": "fixture_workspace/private_fixture_only"}
    shared: dict[str, JsonValue] = {"schema_version": 1, "record_id": record_id}
    shared |= {"sequence": sequence, "stage": stage, "status": status}
    shared |= {"workspace_root": str(root.resolve()), "candidate": candidate}
    shared["publisher"] = publisher
    record = control / "records" / stage / f"{record_id}.json"
    record_value = shared | {"kind": "task10-root-authority-record"}
    record_value |= {"git_state": {"head": head, "entries": [], "canonical_paths": []}}
    record_value |= {"parent_seal": parent_seal, "roles": list(roles)}
    _write(record, record_value)
    seal = control / "seals" / stage / f"{record_id}.json"
    seal_value = shared | {"kind": "task10-root-authority-seal", "parent_seal": parent_seal}
    seal_value["record"] = fixture_artifact(root, record)
    _write(seal, seal_value)
    anchor = control / "anchors" / f"{sequence:020d}.json"
    anchor_value = {"schema_version": 1, "kind": "task10-root-authority-anchor"}
    anchor_value |= {"sequence": sequence, "stage": stage, "prior_anchor": prior_anchor}
    anchor_value |= {"record": fixture_artifact(root, record), "seal": fixture_artifact(root, seal)}
    _write(anchor, anchor_value)
    locator = control / "locator.json"
    locator_value = {"schema_version": 1, "kind": "task10-root-authority-locator"}
    locator_value |= {"workspace_root": str(root.resolve()), "generation": sequence}
    locator_value |= {"active_stage": stage, "prior_anchor": prior_anchor}
    locator_value |= {"active_anchor": fixture_artifact(root, anchor)}
    locator_value["active_seal"] = fixture_artifact(root, seal)
    _write(locator, locator_value)
    paths = dict(previous.role_paths) if previous else {}
    paths.update(role_paths)
    return PrivateAuthority(root, seal, record, locator, paths)


def register_private_baseline(root: Path) -> PrivateAuthority:
    init_private_root(root)
    control = root / CONTROL
    inputs = control / "fixture-inputs"
    provenance = inputs / "provenance.json"
    _write(provenance, {"fixture": True})
    role_paths: dict[str, Path] = {}
    roles: list[dict[str, JsonValue]] = []
    for index, definition in enumerate(BASELINE, 1):
        path = inputs / f"{index:02d}-{definition.name}.json"
        _write(path, _role_value(definition.name))
        role_paths[definition.name] = path
        roles.append(
            {
                "role": definition.name,
                "artifact": fixture_artifact(root, path, definition.content_type),
                "manifest_kind": definition.manifest_kind,
            }
        )
    return append_private_stage(root, "I1_AUTHORITY_BASELINE", "baseline", None, roles, role_paths)


def register_private_scope(root: Path) -> PrivateAuthority:
    baseline1 = register_private_baseline(root)
    rows, paths = replace_private_role(baseline1, "exact_path_release", "baseline2")
    stage = "I1_AUTHORITY_BASELINE"
    baseline2 = append_private_stage(root, stage, "baseline", None, rows, paths, baseline1)
    rows, paths = replace_private_role(baseline2, "exact_path_release", "scope3")
    return append_private_stage(
        root, "I1_AUTHORITY_SCOPE_AMENDED", "scope_amended", None, rows, paths, baseline2
    )


def mutate_json(path: Path, key: str, value: JsonValue) -> None:
    document = json.loads(path.read_text())
    document[key] = value
    _write(path, document)
