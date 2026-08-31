"""Run a configured E2E project gate without expanding shell-controlled input."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from project_gate_runtime import GateProfile, ProjectGateError
from project_gate_runtime import (
    run_profile_with_environment as _run_profile_with_environment,
)

PROFILE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
PROJECT_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
SPEC_PATH = re.compile(r"^e2e/[A-Za-z0-9][A-Za-z0-9._/-]*\.spec\.ts$")
COMMIT_ID = re.compile(r"^[0-9a-f]{40,64}$")
ALLOWED_PROJECTS: dict[str, frozenset[str]] = {
    "scripted": frozenset({"scripted-smoke", "scripted-full", "scripted-capture"}),
    "live": frozenset({"live-manual"}),
}


class GateRequest(NamedTuple):
    profile_name: str
    base_sha: str
    manifest: Path


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise ProjectGateError("invalid_config")
    return value


def _require_count(value: object, *, allow_zero: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectGateError("invalid_config")
    if value < 0 or (value == 0 and not allow_zero):
        raise ProjectGateError("invalid_config")
    return value


def _parse_profile(raw: object) -> GateProfile:
    if not isinstance(raw, dict) or set(raw) != {
        "lane",
        "project",
        "spec",
        "workers",
        "retries",
    }:
        raise ProjectGateError("invalid_config")
    lane = _require_string(raw["lane"])
    project = _require_string(raw["project"])
    spec = _require_string(raw["spec"])
    workers = _require_count(raw["workers"], allow_zero=False)
    retries = _require_count(raw["retries"], allow_zero=True)
    if workers != 1 or retries != 0:
        raise ProjectGateError("invalid_config")
    if lane not in ALLOWED_PROJECTS or not PROJECT_NAME.fullmatch(project):
        raise ProjectGateError("invalid_config")
    valid_spec = SPEC_PATH.fullmatch(spec) and ".." not in spec.split("/")
    if project not in ALLOWED_PROJECTS[lane] or not valid_spec:
        raise ProjectGateError("invalid_config")
    return GateProfile(lane, project, spec, workers, retries)


def load_profiles(config_path: Path) -> dict[str, GateProfile]:
    """Parse the tracked data-only profile table into safe command components."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectGateError("invalid_config") from error
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "profiles"}:
        raise ProjectGateError("invalid_config")
    if raw.get("schema_version") != 1 or not isinstance(raw.get("profiles"), dict):
        raise ProjectGateError("invalid_config")
    parsed: dict[str, GateProfile] = {}
    for name, profile in raw["profiles"].items():
        if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
            raise ProjectGateError("invalid_config")
        parsed[name] = _parse_profile(profile)
    if not parsed:
        raise ProjectGateError("invalid_config")
    return parsed


def _parse_request(arguments: list[str], repo_root: Path) -> GateRequest:
    if len(arguments) != 5 or arguments[1] != "--base-sha" or arguments[3] != "--manifest":
        raise ProjectGateError("usage")
    profile_name = arguments[0]
    base_sha = arguments[2]
    if not PROFILE_NAME.fullmatch(profile_name):
        raise ProjectGateError("unknown_profile")
    if not base_sha:
        raise ProjectGateError("base_not_commit")
    manifest = _safe_new_manifest(repo_root, arguments[4])
    return GateRequest(profile_name, base_sha, manifest)


def _safe_new_manifest(repo_root: Path, raw_path: str) -> Path:
    evidence_root = repo_root / ".omo" / "evidence" / "project-restart-consolidated-roadmap"
    if not evidence_root.exists() or evidence_root.is_symlink() or not evidence_root.is_dir():
        raise ProjectGateError("unsafe_manifest")
    trusted_root = evidence_root.resolve(strict=True)
    candidate = Path(raw_path)
    if ".." in candidate.parts:
        raise ProjectGateError("unsafe_manifest")
    resolved_candidate = candidate if candidate.is_absolute() else repo_root / candidate
    try:
        relative = resolved_candidate.relative_to(trusted_root)
    except ValueError as error:
        raise ProjectGateError("unsafe_manifest") from error
    if len(relative.parts) != 1 or resolved_candidate.suffix != ".json":
        raise ProjectGateError("unsafe_manifest")
    try:
        resolved_candidate.lstat()
    except FileNotFoundError:
        return resolved_candidate
    except OSError as error:
        raise ProjectGateError("unsafe_manifest") from error
    if resolved_candidate.is_symlink():
        raise ProjectGateError("unsafe_manifest")
    raise ProjectGateError("manifest_exists")


def _resolve_base_sha(base_sha: str, repo_root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise ProjectGateError("git_start_failed")
    result = subprocess.run(  # noqa: S603 - fixed Git query with a commit-shaped revision
        [git, "rev-parse", "--verify", f"{base_sha}^{{commit}}"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    resolved = result.stdout.strip()
    if result.returncode != 0 or not COMMIT_ID.fullmatch(resolved):
        raise ProjectGateError("base_not_commit")
    ancestor = subprocess.run(  # noqa: S603 - fixed Git ancestry query with resolved commit ID
        [git, "merge-base", "--is-ancestor", resolved, "HEAD"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if ancestor.returncode != 0:
        raise ProjectGateError("base_not_ancestor")
    return resolved


def run(arguments: list[str], repo_root: Path, inherited_environment: dict[str, str]) -> int:
    """Validate the public request, then delegate resource ownership to the lane runner."""
    request = _parse_request(arguments, repo_root)
    profiles = load_profiles(repo_root / "scripts" / "project-gates.json")
    profile = profiles.get(request.profile_name)
    if profile is None:
        raise ProjectGateError("unknown_profile")
    base_sha = _resolve_base_sha(request.base_sha, repo_root)
    return _run_profile_with_environment(
        request.profile_name,
        profile,
        base_sha,
        request.manifest,
        repo_root,
        inherited_environment,
    )


def main(arguments: list[str]) -> int:
    """Render stable CLI errors without exposing user-provided paths or command fragments."""
    repo_root = Path(__file__).resolve().parents[1]
    try:
        return run(arguments, repo_root, dict(os.environ))
    except ProjectGateError as error:
        print(f"project_gate_error:{error}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
