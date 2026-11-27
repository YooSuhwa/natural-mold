"""Stage an immutable product revision and the cutover probe files."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from importlib import metadata
from pathlib import Path

import tomllib
import yaml

_SDK_HELPER = "frontend/e2e/helpers/ag-ui-sdk-verification.ts"
_BROWSER_FILES = (
    "frontend/e2e/ag-ui-approval-readiness.spec.ts",
    "frontend/e2e/manual/chat-ag-ui-cutover.spec.ts",
    "frontend/e2e/helpers/ag-ui-cutover.ts",
    _SDK_HELPER,
)


def revision(source: Path, ref: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        text=True,
    ).strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_probe_files(here: Path, run_root: Path) -> Path:
    """Freeze local acceptance sources before any child processes are launched."""
    here = here.resolve(strict=True)
    run_root = run_root.resolve(strict=True)
    harness = run_root / "acceptance-harness"
    harness.mkdir(mode=0o700)
    probes = harness / "scripts/ag_ui_acceptance"
    files = {
        source.relative_to(here).as_posix(): (source, probes / source.relative_to(here))
        for source in here.rglob("*")
        if source.suffix in {".py", ".cjs", ".mjs"} and source.is_file()
    }
    for rel in _BROWSER_FILES:
        source = here.parents[1] / rel
        if source.is_file():
            files[rel] = (source, harness / rel)
    checksums = {}
    for name, (source, target) in sorted(files.items()):
        if source.is_symlink():
            raise ValueError(f"Acceptance probe must be a regular source file: {name}")
        # Hash precisely the bytes we write, even if a checkout changes during staging.
        payload = source.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        target.chmod(0o400)
        checksums[name] = hashlib.sha256(payload).hexdigest()
    receipt = run_root / "probe-files.json"
    with receipt.open("x") as output:
        output.write(json.dumps(checksums, indent=2, sort_keys=True) + "\n")
    receipt.chmod(0o400)
    return probes


def _python_versions(repo: Path, require_protocol: bool) -> dict[str, str]:
    expected = {"deepagents": "0.7.42", "langgraph": "1.2.24"}
    with (repo / "backend/uv.lock").open("rb") as source:
        packages = tomllib.load(source)["package"]
    locked = {row["name"]: row["version"] for row in packages}
    if require_protocol or "ag-ui-protocol" in locked:
        expected["ag-ui-protocol"] = "1.0.0"
    for name, target in expected.items():
        if locked.get(name) != target:
            raise ValueError(
                f"Acceptance requires {name} {target} in the source revision's uv.lock"
            )
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            raise ValueError(f"{name} is absent from the selected Python environment") from None
        if installed != target:
            raise ValueError(
                f"{name}: source lock requires {target}, selected Python has {installed}"
            )
    return expected


def _skill_graph(lock: dict) -> dict:
    """Identify exactly the skill importer's resolutions including all transitives."""
    importer = lock["importers"]["backend/skill-node"]
    result = {"importer": importer}
    pending = [(name, ref["version"]) for name, ref in importer["dependencies"].items()]
    while pending:
        name, reference = pending.pop()
        ident = f"{name}@{reference}"
        if ident in result:
            continue
        snapshot = lock["snapshots"][ident]
        result[ident] = (lock["packages"][ident.split("(", 1)[0]], snapshot)
        for field in ("dependencies", "optionalDependencies"):
            pending.extend(snapshot.get(field, {}).items())
    return result


def _installed_version(modules: Path, store: Path, name: str, version: str) -> str:
    package = modules / name / "package.json"
    if not package.is_file() or not package.resolve().is_relative_to(store):
        raise ValueError(f"{name} is absent or resolves outside its verified pnpm store")
    manifest = json.loads(package.read_bytes())
    if manifest["name"] != name or manifest["version"] != version:
        raise ValueError(f"{name}: installed manifest disagrees with the locked {version}")
    return version


def _node_modules(repo: Path, deps: Path, skill_deps: Path, require_protocol: bool) -> dict:
    source_lock = repo / "pnpm-lock.yaml"
    lock_digest = digest(source_lock)
    store = (deps / "node_modules/.pnpm").resolve()
    if not (store / "lock.yaml").is_file() or digest(store / "lock.yaml") != lock_digest:
        raise ValueError("The installed frontend pnpm store lock differs from the source revision")
    if digest(deps / "pnpm-lock.yaml") != lock_digest:
        raise ValueError("Frontend dependency checkout lock differs from the source revision")
    lock = yaml.safe_load(source_lock.read_bytes())
    frontend = deps / "frontend/node_modules"
    importer = lock["importers"]["frontend"]
    refs = importer["dependencies"] | importer["devDependencies"]
    names = ["react", "next", "@assistant-ui/react", "@playwright/test"]
    if require_protocol:
        names.extend(["@ag-ui/core", "@ag-ui/client", "@assistant-ui/react-ag-ui"])
    frontend_versions = {}
    for name in names:
        if name not in refs:
            raise ValueError(f"Frontend source has no locked {name}")
        version = refs[name]["version"].split("(", 1)[0]
        frontend_versions[name] = _installed_version(frontend, store, name, version)

    skill_modules = skill_deps / "backend/skill-node/node_modules"
    skill_store = (skill_deps / "node_modules/.pnpm").resolve()
    skill_lock = skill_store / "lock.yaml"
    if not skill_lock.is_file():
        raise ValueError("Missing skill dependency store; select its checkout with --skill-deps")
    if digest(skill_lock) != lock_digest and _skill_graph(
        yaml.safe_load(skill_lock.read_bytes())
    ) != _skill_graph(lock):
        raise ValueError("Skill npm dependency graph differs from the source revision")
    skill_versions = {}
    for name, ref in lock["importers"]["backend/skill-node"]["dependencies"].items():
        reference = ref["version"]
        entry = lock["packages"][f"{name}@{reference.split('(', 1)[0]}"]
        if reference.startswith("file:"):
            archive = repo / reference.removeprefix("file:")
            checksum = base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
            if f"sha512-{checksum}" != entry["resolution"]["integrity"]:
                raise ValueError(f"{name} vendored package integrity differs from pnpm-lock.yaml")
        version = entry.get("version", reference.split("(", 1)[0])
        skill_versions[name] = _installed_version(skill_modules, skill_store, name, version)
    for rel, modules in (
        ("node_modules", deps / "node_modules"),
        ("frontend/node_modules", frontend),
        ("backend/skill-node/node_modules", skill_modules),
    ):
        (repo / rel).symlink_to(modules.resolve(strict=True), target_is_directory=True)
    return {
        "pnpm_lock_sha256": lock_digest,
        "frontend": frontend_versions,
        "skills": skill_versions,
    }


def stage(
    source: Path,
    sha: str,
    deps: Path,
    root: Path,
    harness: Path,
    browser: bool,
    *,
    skill_deps: Path | None = None,
    require_protocol: bool = True,
) -> Path:
    repo = root / "source"
    repo.mkdir()
    # git archive excludes .env, DB/workspace state, node_modules, and uncommitted changes.
    producer = subprocess.Popen(["git", "-C", str(source), "archive", sha], stdout=subprocess.PIPE)
    try:
        assert producer.stdout
        with tarfile.open(fileobj=producer.stdout, mode="r|") as tar:
            tar.extractall(repo, filter="data")
    finally:
        if producer.stdout:
            producer.stdout.close()
        producer.wait()
    if producer.returncode:
        raise RuntimeError("Cannot stage product revision")
    python = _python_versions(repo, require_protocol)
    modules = _node_modules(repo, deps, skill_deps or deps, require_protocol)
    sdk_verifier = "scripts/ag_ui_acceptance/verify_transcripts.mjs"
    browser_files = _BROWSER_FILES if browser else ()
    sdk_files = (_SDK_HELPER, sdk_verifier) if require_protocol else ()
    probes = {rel: digest(harness / rel) for rel in (*browser_files, *sdk_files)}
    for rel, checksum in probes.items():
        target = repo / rel
        if not target.is_file() or digest(target) != checksum:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(harness / rel, target)
    subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/prepare-isolated-run.py"),
            str(root),
            str(repo),
        ],
        check=True,
        capture_output=True,
    )
    if (deps / "node_modules").is_dir():
        (root / "node_modules").symlink_to(deps / "node_modules", target_is_directory=True)
    if require_protocol:
        # The standard preparation step mirrors only frontend sources.
        (root / sdk_verifier).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / sdk_verifier, root / sdk_verifier)
    for rel, checksum in probes.items():
        if digest(root / rel) != checksum:
            raise RuntimeError(f"Staged probe differs from the acceptance harness: {rel}")
    (root / "source-dependencies.json").write_text(
        json.dumps(
            {
                "python": python,
                "uv_lock_sha256": digest(repo / "backend/uv.lock"),
                **modules,
                "browser_probes_sha256": {rel: probes[rel] for rel in browser_files},
                "sdk_probes_sha256": {rel: probes[rel] for rel in sdk_files},
            },
            indent=2,
        )
        + "\n"
    )
    return repo
