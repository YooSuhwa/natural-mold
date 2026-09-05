"""Run a configured E2E project gate without expanding shell-controlled input."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple, assert_never

from project_gate_catalog import CANONICAL_WAVES, CATALOG
from project_gate_composite import run_composite
from project_gate_process import safe_environment
from project_gate_runtime import (
    LIVE_LLM_ENVIRONMENT_NAMES,
    OWNED_LOOPBACK_OPT_IN,
    CompositeProfile,
    GateProfile,
    JSONValue,
    ProjectGateError,
)
from project_gate_runtime import (
    run_profile_with_environment as _run_profile_with_environment,
)
from project_gate_toolchain import resolve_base_sha as _resolve_base_sha
from project_gate_toolchain import (
    resolve_provenance,
    resolve_toolchain,
    verify_toolchain,
)

PROFILE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
PROJECT_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
SPEC_PATH = re.compile(r"^e2e/[A-Za-z0-9][A-Za-z0-9._/-]*\.spec\.ts$")
ALLOWED_PROJECTS: dict[str, frozenset[str]] = {
    "scripted": frozenset({"scripted-smoke", "scripted-full", "scripted-capture"}),
    "live": frozenset({"live-manual"}),
}


class GateRequest(NamedTuple):
    profile_name: str
    base_sha: str
    manifest: Path


def _require_string(value: JSONValue) -> str:
    if not isinstance(value, str):
        raise ProjectGateError("invalid_config")
    return value


def _require_count(value: JSONValue, *, allow_zero: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectGateError("invalid_config")
    if value < 0 or (value == 0 and not allow_zero):
        raise ProjectGateError("invalid_config")
    return value


def _parse_profile(raw: JSONValue) -> GateProfile:
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


def _parse_composite(name: str, raw: JSONValue) -> CompositeProfile:
    if not isinstance(raw, dict) or set(raw) != {"kind", "nodes"} or raw.get("kind") != "composite":
        raise ProjectGateError("invalid_config")
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        raise ProjectGateError("invalid_config")
    parsed_nodes: list[str] = []
    for node in nodes:
        if not isinstance(node, str):
            raise ProjectGateError("invalid_config")
        parsed_nodes.append(node)
    parsed = tuple(parsed_nodes)
    if (
        len(parsed) != len(set(parsed))
        or name not in CANONICAL_WAVES
        or parsed != CANONICAL_WAVES[name]
        or any(node not in CATALOG for node in parsed)
    ):
        raise ProjectGateError("invalid_config")
    return CompositeProfile(parsed)


def load_profiles(config_path: Path) -> dict[str, GateProfile | CompositeProfile]:
    """Parse the tracked data-only profile table into safe command components."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectGateError("invalid_config") from error
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "profiles"}:
        raise ProjectGateError("invalid_config")
    if raw.get("schema_version") not in {1, 2} or not isinstance(raw.get("profiles"), dict):
        raise ProjectGateError("invalid_config")
    schema_version = raw["schema_version"]
    parsed: dict[str, GateProfile | CompositeProfile] = {}
    for name, profile in raw["profiles"].items():
        if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
            raise ProjectGateError("invalid_config")
        if schema_version == 1 or name == "smoke":
            parsed[name] = _parse_profile(profile)
        else:
            parsed[name] = _parse_composite(name, profile)
    required = {"smoke", *CANONICAL_WAVES}
    if not parsed or (schema_version == 2 and set(parsed) != required):
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
    manifest = _safe_new_manifest(repo_root, arguments[4], profile_name)
    return GateRequest(profile_name, base_sha, manifest)


def _safe_new_manifest(repo_root: Path, raw_path: str, profile_name: str) -> Path:
    evidence_root = repo_root / ".omo" / "evidence" / "project-restart-consolidated-roadmap"
    if not evidence_root.exists() or evidence_root.is_symlink() or not evidence_root.is_dir():
        raise ProjectGateError("unsafe_manifest")
    trusted_root = evidence_root.resolve(strict=True)
    candidate = Path(raw_path)
    if ".." in candidate.parts:
        raise ProjectGateError("unsafe_manifest")
    resolved_candidate = candidate if candidate.is_absolute() else repo_root / candidate
    direct = resolved_candidate.parent == trusted_root
    final = (
        profile_name == "final-static"
        and resolved_candidate.name == "f2-static.json"
        and resolved_candidate.parent.parent == trusted_root / "final-attempts"
        and re.fullmatch(r"[0-9a-f]{64}", resolved_candidate.parent.name) is not None
    )
    if (not direct and not final) or resolved_candidate.suffix != ".json":
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


def run(arguments: list[str], repo_root: Path, inherited_environment: dict[str, str]) -> int:
    """Validate the public request, then delegate resource ownership to the lane runner."""
    request = _parse_request(arguments, repo_root)
    profiles = load_profiles(repo_root / "scripts" / "project-gates.json")
    profile = profiles.get(request.profile_name)
    if profile is None:
        raise ProjectGateError("unknown_profile")
    match profile:
        case CompositeProfile(nodes=nodes):
            toolchain, runtime = resolve_toolchain(repo_root)
            provenance = resolve_provenance(request.base_sha, repo_root)
            return run_composite(
                request.profile_name,
                nodes,
                provenance,
                toolchain,
                runtime,
                request.manifest,
                repo_root,
                inherited_environment,
            )
        case GateProfile():
            toolchain, _runtime = resolve_toolchain(repo_root)
            environment = safe_environment(inherited_environment, toolchain)
            if profile.lane == "live":
                environment.update(
                    {
                        name: inherited_environment[name]
                        for name in LIVE_LLM_ENVIRONMENT_NAMES
                        if name in inherited_environment
                    }
                )
                if inherited_environment.get(OWNED_LOOPBACK_OPT_IN) == "1":
                    environment[OWNED_LOOPBACK_OPT_IN] = "1"
            verify_toolchain(toolchain)
            base_sha = _resolve_base_sha(request.base_sha, repo_root, toolchain.git)
            verify_toolchain(toolchain)
            return _run_profile_with_environment(
                request.profile_name,
                profile,
                base_sha,
                request.manifest,
                repo_root,
                environment,
                trusted_pnpm=toolchain.pnpm,
            )
        case _:
            assert_never(profile)


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
