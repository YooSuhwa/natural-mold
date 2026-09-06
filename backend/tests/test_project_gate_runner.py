"""Boundary tests for the reusable project-gate runner."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import TypeGuard
from uuid import uuid4

import pytest

from tests.project_gate_wave_support import synthetic_docker_identity

SOURCE_REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOURCE_REPO_ROOT
RUNNER_PATH = REPO_ROOT / "scripts" / "project_gate_runner.py"
WRAPPER_PATH = REPO_ROOT / "scripts" / "run-project-gate.sh"
EVIDENCE_ROOT = REPO_ROOT / ".omo" / "evidence" / "project-restart-consolidated-roadmap"


@pytest.fixture(scope="module")
def project_gate_runner() -> ModuleType:
    """Load the standalone runner and its sibling runtime module for this test module."""
    sys.path.insert(0, str(RUNNER_PATH.parent))
    spec = importlib.util.spec_from_file_location("project_gate_runner", RUNNER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_project_gate_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every public gate scenario in a disposable checkout-shaped repository.

    The production evidence root is intentionally ignored and may be absent in
    clean CI.  More importantly, mutating it from parallel test workers races
    real lifecycle tests.  The runner still sees its exact on-disk contract;
    only the test-owned repository root changes.
    """
    repo_root = tmp_path / "repo"
    shutil.copytree(SOURCE_REPO_ROOT / "scripts", repo_root / "scripts")
    python_path = repo_root / "backend" / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.symlink_to(SOURCE_REPO_ROOT / "backend" / ".venv" / "bin" / "python")
    (python_path.parent.parent / "pyvenv.cfg").symlink_to(
        SOURCE_REPO_ROOT / "backend" / ".venv" / "pyvenv.cfg"
    )
    (python_path.parent.parent / "lib").symlink_to(SOURCE_REPO_ROOT / "backend" / ".venv" / "lib")
    evidence_root = repo_root / ".omo" / "evidence" / "project-restart-consolidated-roadmap"
    evidence_root.mkdir(parents=True)
    (repo_root / ".gitignore").write_text("output/\n", encoding="utf-8")

    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        sys.modules[__name__], "WRAPPER_PATH", repo_root / "scripts/run-project-gate.sh"
    )
    monkeypatch.setattr(sys.modules[__name__], "EVIDENCE_ROOT", evidence_root)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_commands(
    tmp_path: Path,
    *,
    git_mode: str = "ok",
    pnpm_exit: int = 0,
    pnpm_expected_argv: list[str] | None = None,
    pnpm_manifest_mode: str = "valid",
) -> tuple[Path, dict[str, str]]:
    """Create observable git/pnpm process fakes for a single runner invocation."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "process-log.jsonl"
    log_literal = json.dumps(str(log_path))
    git_mode_literal = json.dumps(git_mode)
    expected_argv_literal = (
        json.dumps(pnpm_expected_argv) if pnpm_expected_argv is not None else "None"
    )
    manifest_mode_literal = json.dumps(pnpm_manifest_mode)
    _write_executable(
        bin_dir / "git",
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"with open({log_literal}, 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'command': 'git', 'argv': sys.argv[1:]}) + '\\n')\n"
        f"mode = {git_mode_literal}\n"
        "if sys.argv[1:3] == ['rev-parse', '--verify']:\n"
        "    if mode == 'invalid': sys.exit(1)\n"
        "    print('a' * 40)\n"
        "    sys.exit(0)\n"
        "if sys.argv[1:3] == ['merge-base', '--is-ancestor']:\n"
        "    sys.exit(1 if mode == 'nonancestor' else 0)\n"
        "sys.exit(0)\n",
    )
    _write_executable(
        bin_dir / "pnpm",
        "#!/usr/bin/env python3\n"
        "import hashlib, json, os, sys\n"
        "from pathlib import Path\n"
        f"with open({log_literal}, 'a', encoding='utf-8') as handle:\n"
        "    event = {'command': 'pnpm', 'argv': sys.argv[1:]}\n"
        "    event['manifest'] = os.environ.get('E2E_RUN_MANIFEST')\n"
        "    event['slug'] = os.environ.get('E2E_EXPORT_SLUG')\n"
        "    event['owned_loopback'] = os.environ.get('E2E_EGRESS_ALLOW_OWNED_LOOPBACK')\n"
        "    event['environment_keys'] = sorted(os.environ)\n"
        "    handle.write(json.dumps(event) + '\\n')\n"
        f"expected = {expected_argv_literal}\n"
        "if expected is not None and sys.argv[1:] != expected: sys.exit(89)\n"
        f"manifest_mode = {manifest_mode_literal}\n"
        f"if {pnpm_exit} == 0 and manifest_mode != 'missing':\n"
        "    manifest_path = Path(os.environ['E2E_RUN_MANIFEST'])\n"
        "    if manifest_mode == 'forged':\n"
        "        manifest_path.write_text('{}', encoding='utf-8')\n"
        "    else:\n"
        "        project = next(value.split('=', 1)[1] for value in sys.argv[1:] if value.startswith('--project='))\n"  # noqa: E501
        "        lane = 'live' if project == 'live-manual' else 'scripted'\n"
        "        spec = next(value for value in sys.argv[1:] if value.startswith('e2e/'))\n"
        "        if lane == 'live':\n"
        "            nodes = ['live-manual::e2e/agent-triggers.spec.ts::a created interval trigger renders in the settings triggers tab', 'live-manual::e2e/builder.spec.ts::starts a session and runs the build pipeline from an initial message', 'live-manual::e2e/operator-screens.spec.ts::System LLM shows the seed-configured role slots', 'live-manual::e2e/operator-screens.spec.ts::creates and deletes a system credential through the catalog modal']\n"  # noqa: E501
        "        else:\n"
        "            nodes = [f'{project}::e2e/{spec.split(\"/\", 1)[1]}::works']\n"
        '        artifact = b\'{"result":"passed"}\\n\'\n'
        "        export_relative = f'output/e2e-captures/{os.environ[\"E2E_EXPORT_SLUG\"]}-fake'\n"
        "        export_directory = manifest_path.parents[3] / export_relative\n"
        "        (export_directory / 'results').mkdir(parents=True, exist_ok=True)\n"
        "        (export_directory / 'results/execution.json').write_bytes(artifact)\n"
        "        artifact_entry = {'path': 'results/execution.json', 'sha256': hashlib.sha256(artifact).hexdigest(), 'size_bytes': len(artifact)}\n"  # noqa: E501
        "        export_manifest = {'schema_version': 1, 'project': project, 'policy': {'version': 1, 'screenshots': 'scripted-capture-only', 'max_file_bytes': 20 * 1024 * 1024, 'max_total_bytes': 50 * 1024 * 1024}, 'secret_scan': {'passed': True, 'exact_secret_count': 0}, 'files': [artifact_entry], 'total': {'file_count': 1, 'size_bytes': len(artifact)}}\n"  # noqa: E501
        "        export_bytes = (json.dumps(export_manifest, sort_keys=True) + '\\n').encode()\n"
        "        (export_directory / 'export-manifest.json').write_bytes(export_bytes)\n"
        "        manifest_entry = {'path': 'export-manifest.json', 'sha256': hashlib.sha256(export_bytes).hexdigest(), 'size_bytes': len(export_bytes)}\n"  # noqa: E501
        "        egress = {'enabled': False, 'clean_stop': True, 'records': []}\n"
        "        if lane == 'live':\n"
        "            egress = {'enabled': True, 'clean_stop': True, 'records': [{'method': 'POST', 'origin': 'https://api.example.com', 'path_class': 'chat_completions', 'status': 200, 'count': 1}]}\n"  # noqa: E501
        "        payload = {'schema_version': 1, 'runner': 'moldy-isolated-e2e', 'lane': lane, 'project': project, 'workers': 1, 'retries': 0, 'reuse_existing_server': False, 'status': 'passed', 'failure_reason': None, 'child_exit_code': 0, 'self_test': 'normal', 'run_id': 'a' * 24, 'owned_run_root': True, 'owned_database': True, 'owned_backend': True, 'owned_frontend': True, 'owned_proxy': lane == 'live', 'postgres_image': 'postgres:16-alpine', 'server_version_num': '160001', 'alembic_head': 'm70', 'alembic_current': 'm70', 'schema_fingerprint': 'b' * 64, 'second_upgrade_idempotent': True, 'frontend_port': 3100 if lane == 'scripted' else 3200, 'backend_port': 8101 if lane == 'scripted' else 8201, 'selected_ids': nodes, 'executed_ids': nodes, 'export': {'schema_version': 1, 'secret_scan_passed': True, 'export_directory': export_relative, 'manifest': manifest_entry, 'files': [manifest_entry, artifact_entry], 'screenshots': []}, 'egress': egress, 'cleanup': {'cleanup_container_removed': True, 'owned_label_absent': True, 'postgres_port_removed': True, 'owned_database_removed': True, 'backend_port_removed': True, 'frontend_port_removed': True, 'proxy_port_removed': True, 'process_group_stopped': True, 'cleanup_run_root_removed': True, 'foreign_containers_preserved': True}}\n"  # noqa: E501
        "        manifest_path.write_text(json.dumps(payload), encoding='utf-8')\n"
        f"sys.exit({pnpm_exit})\n",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
    environment["PROJECT_GATE_TEST_LOG"] = str(log_path)
    return log_path, environment


def _manifest_path() -> Path:
    return EVIDENCE_ROOT / f"test-project-gate-{uuid4().hex}.json"


def _run_wrapper(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["/bin/bash", str(WRAPPER_PATH), *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    log_path = Path(environment["PROJECT_GATE_TEST_LOG"])
    if log_path.exists():
        for call in _read_log(log_path):
            slug = call.get("slug")
            if isinstance(slug, str):
                shutil.rmtree(
                    REPO_ROOT / "output" / "e2e-captures" / f"{slug}-fake", ignore_errors=True
                )
    manifest_index = arguments.index("--manifest")
    manifest = Path(arguments[manifest_index + 1])
    if not manifest.is_absolute():
        manifest = REPO_ROOT / manifest
    manifest.unlink(missing_ok=True)
    return result


def _read_log(log_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def _is_string_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _fake_pnpm_path(environment: dict[str, str]) -> Path:
    """Return the test-only lifecycle executable created at the first PATH entry."""
    return Path(environment["PATH"].split(os.pathsep, 1)[0]) / "pnpm"


def _remove_fake_exports(log_path: Path, manifest: Path) -> None:
    """Remove the fake lifecycle's owned artifacts after direct-boundary assertions."""
    if log_path.exists():
        for call in _read_log(log_path):
            slug = call.get("slug")
            if isinstance(slug, str):
                shutil.rmtree(
                    REPO_ROOT / "output" / "e2e-captures" / f"{slug}-fake", ignore_errors=True
                )
    manifest.unlink(missing_ok=True)


def _direct_gate_toolchain(project_gate_runner: ModuleType, *, identity_count: int) -> object:
    """Build a captured toolchain shape for the public GateProfile boundary."""
    toolchain_module = sys.modules[project_gate_runner.resolve_toolchain.__module__]
    docker = Path("/usr/local/bin/docker")
    identities = tuple(synthetic_docker_identity(docker) for _ in range(identity_count))
    return toolchain_module.TrustedToolchain(
        python=Path("/trusted/python"),
        node=Path("/trusted/node"),
        pnpm=Path("/trusted/pnpm"),
        uv=Path("/trusted/uv"),
        home=Path("/trusted/home"),
        user="trusted-user",
        docker=docker,
        system_paths=("/usr/bin", "/bin"),
        runtime_paths=("/trusted/runtime",),
        identities=identities,
        git=Path("/trusted/git"),
    )


def _runner_subprocess(project_gate_runner: ModuleType) -> ModuleType:
    """Return the process module shared by trusted Git and direct lane execution."""
    runtime = sys.modules[project_gate_runner._run_profile_with_environment.__module__]
    return runtime.subprocess


@pytest.mark.parametrize(
    "ambient_docker",
    [
        {},
        {
            "MOLDY_GATE_DOCKER": "/attacker/docker",
            "MOLDY_GATE_DOCKER_IDENTITY": "b" * 64,
        },
    ],
    ids=("missing-ambient-token", "hostile-ambient-pair"),
)
def test_public_gate_profile_uses_captured_toolchain_environment(
    project_gate_runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    ambient_docker: dict[str, str],
) -> None:
    """Given ambient Docker values, public smoke passes only captured values to children."""
    manifest = _manifest_path()
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )
    trusted = _direct_gate_toolchain(project_gate_runner, identity_count=1)
    calls: list[tuple[list[str], dict[str, str]]] = []
    verification_calls: list[object] = []

    def fake_run(
        command: list[str], *, env: dict[str, str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, env))
        if command[:3] == ["/trusted/git", "rev-parse", "--verify"]:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40, stderr="")
        if command[:3] == ["/trusted/git", "merge-base", "--is-ancestor"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] != "/trusted/pnpm":
            manifest.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def verify_captured(toolchain: object) -> None:
        verification_calls.append(toolchain)

    inherited = {
        "PATH": "/attacker/bin",
        "DOCKER_HOST": "tcp://attacker.example.test:2375",
        "DOCKER_CONFIG": "/attacker/docker-config",
        "DOCKER_CONTEXT": "attacker-context",
        "OPENAI_API_KEY": "sentinel-openai",
        **ambient_docker,
    }
    monkeypatch.setattr(project_gate_runner, "load_profiles", lambda _path: {"smoke": profile})
    monkeypatch.setattr(project_gate_runner, "resolve_toolchain", lambda _root: (trusted, {}))
    monkeypatch.setattr(project_gate_runner, "verify_toolchain", verify_captured)
    monkeypatch.setattr(_runner_subprocess(project_gate_runner), "run", fake_run)
    monkeypatch.setenv("PATH", "/attacker/bin")

    try:
        result = project_gate_runner.run(
            ["smoke", "--base-sha", "a" * 40, "--manifest", str(manifest)],
            REPO_ROOT,
            inherited,
        )
    finally:
        manifest.unlink(missing_ok=True)

    assert result == 0
    assert verification_calls == [trusted, trusted]
    assert len(calls) == 4
    assert calls[0][0] == ["/trusted/git", "rev-parse", "--verify", f"{'a' * 40}^{{commit}}"]
    assert calls[1][0] == [
        "/trusted/git",
        "merge-base",
        "--is-ancestor",
        "a" * 40,
        "HEAD",
    ]
    assert calls[0][1] == {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
    assert calls[1][1] == calls[0][1]
    pnpm_command, pnpm_environment = calls[2]
    assert pnpm_command[0] == "/trusted/pnpm"
    assert pnpm_environment["PATH"] == "/trusted/runtime:/usr/bin:/bin"
    assert pnpm_environment["MOLDY_GATE_DOCKER"] == "/usr/local/bin/docker"
    assert pnpm_environment["MOLDY_GATE_DOCKER_IDENTITY"] != ambient_docker.get(
        "MOLDY_GATE_DOCKER_IDENTITY"
    )
    assert {"DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT", "OPENAI_API_KEY"}.isdisjoint(
        pnpm_environment
    )
    checker_command, checker_environment = calls[3]
    assert checker_command[-1] == str(manifest)
    assert checker_environment["MOLDY_GATE_DOCKER"] == "/usr/local/bin/docker"
    assert (
        checker_environment["MOLDY_GATE_DOCKER_IDENTITY"]
        == pnpm_environment["MOLDY_GATE_DOCKER_IDENTITY"]
    )


@pytest.mark.parametrize("identity_count", [0, 2], ids=("missing", "ambiguous"))
def test_public_gate_profile_rejects_missing_or_ambiguous_docker_identity_before_child(
    project_gate_runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    identity_count: int,
) -> None:
    """Given an incomplete captured toolchain, when public smoke starts, then no child launches."""
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )
    trusted = _direct_gate_toolchain(project_gate_runner, identity_count=identity_count)

    def unexpected_subprocess(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("child subprocess launched before Docker identity preflight")

    monkeypatch.setattr(project_gate_runner, "load_profiles", lambda _path: {"smoke": profile})
    monkeypatch.setattr(project_gate_runner, "resolve_toolchain", lambda _root: (trusted, {}))
    monkeypatch.setattr(_runner_subprocess(project_gate_runner), "run", unexpected_subprocess)

    with pytest.raises(project_gate_runner.ProjectGateError, match="runtime_preflight_failed"):
        project_gate_runner.run(
            ["smoke", "--base-sha", "a" * 40, "--manifest", str(_manifest_path())],
            REPO_ROOT,
            {},
        )


def test_public_gate_profile_rechecks_toolchain_after_base_resolution(
    tmp_path: Path,
    project_gate_runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a captured pnpm changes during base resolution, public smoke starts no child."""
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )
    trusted = _direct_gate_toolchain(project_gate_runner, identity_count=1)
    captured_pnpm = tmp_path / "pnpm"
    captured_pnpm.write_text("original", encoding="utf-8")

    def mutate_pnpm(_base: str, _root: Path, _git: Path) -> str:
        captured_pnpm.write_text("mutated", encoding="utf-8")
        return "a" * 40

    def verify_after_mutation(_toolchain: object) -> None:
        if captured_pnpm.read_text(encoding="utf-8") == "mutated":
            raise project_gate_runner.ProjectGateError("runtime_changed")

    def unexpected_subprocess(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("child subprocess launched after toolchain mutation")

    monkeypatch.setattr(project_gate_runner, "load_profiles", lambda _path: {"smoke": profile})
    monkeypatch.setattr(project_gate_runner, "resolve_toolchain", lambda _root: (trusted, {}))
    monkeypatch.setattr(project_gate_runner, "_resolve_base_sha", mutate_pnpm)
    monkeypatch.setattr(
        project_gate_runner, "verify_toolchain", verify_after_mutation, raising=False
    )
    monkeypatch.setattr(_runner_subprocess(project_gate_runner), "run", unexpected_subprocess)

    with pytest.raises(project_gate_runner.ProjectGateError, match="runtime_changed"):
        project_gate_runner.run(
            ["smoke", "--base-sha", "a" * 40, "--manifest", str(_manifest_path())],
            REPO_ROOT,
            {},
        )


def test_runner_invokes_exact_smoke_argv_and_runner_owned_environment(
    tmp_path: Path, project_gate_runner: ModuleType
) -> None:
    """Given a valid smoke gate, when run, then pnpm receives fixed argv and safe exports."""
    log_path, environment = _fake_commands(tmp_path)
    manifest = _manifest_path()

    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )
    try:
        result = project_gate_runner._run_profile_with_environment(
            "smoke",
            profile,
            "a" * 40,
            manifest,
            REPO_ROOT,
            environment,
            trusted_pnpm=_fake_pnpm_path(environment),
        )
    finally:
        _remove_fake_exports(log_path, manifest)

    assert result == 0
    calls = _read_log(log_path)
    pnpm = next(call for call in calls if call["command"] == "pnpm")
    assert pnpm["argv"] == [
        "--dir",
        "frontend",
        "test:e2e:scripted",
        "--",
        "--project=scripted-smoke",
        "e2e/smoke.spec.ts",
    ]
    assert pnpm["manifest"] == str(manifest.resolve())
    assert isinstance(pnpm["slug"], str)
    assert re.fullmatch(r"project-gate-smoke-a{12}-[0-9a-f]{16}", pnpm["slug"])
    assert not manifest.exists()


def test_runner_argv_is_accepted_by_lifecycle_boundary(
    tmp_path: Path, project_gate_runner: ModuleType
) -> None:
    """Given the real lifecycle contract, when run, then no rejected overrides cross pnpm."""
    expected_argv = [
        "--dir",
        "frontend",
        "test:e2e:scripted",
        "--",
        "--project=scripted-smoke",
        "e2e/smoke.spec.ts",
    ]
    log_path, environment = _fake_commands(tmp_path, pnpm_expected_argv=expected_argv)

    manifest = _manifest_path()
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )
    try:
        result = project_gate_runner._run_profile_with_environment(
            "smoke",
            profile,
            "a" * 40,
            manifest,
            REPO_ROOT,
            environment,
            trusted_pnpm=_fake_pnpm_path(environment),
        )
    finally:
        _remove_fake_exports(log_path, manifest)

    assert result == 0
    pnpm = next(call for call in _read_log(log_path) if call["command"] == "pnpm")
    assert pnpm["argv"] == expected_argv


def test_runner_sanitizes_scripted_pnpm_environment(
    tmp_path: Path, project_gate_runner: ModuleType
) -> None:
    """Given provider secrets in the caller, when scripted starts, then pnpm sees only safe env."""
    log_path, environment = _fake_commands(tmp_path)
    environment.update(
        {
            "OPENAI_API_KEY": "sentinel-openai",
            "TAVILY_API_KEY": "sentinel-tavily",
            "LANGCHAIN_API_KEY": "sentinel-langchain",
            "LANGFUSE_SECRET_KEY": "sentinel-langfuse",
            "NODE_OPTIONS": "--require=/tmp/sentinel",
            "E2E_LLM_BASE_URL": "https://should-not-pass.example.test/v1",
            "E2E_LLM_API_KEY": "should-not-pass",
            "E2E_LLM_MODEL": "should-not-pass",
            "PNPM_HOME": "/tmp/pnpm-home",
            "DOCKER_CONTEXT": "sentinel-context",
        }
    )

    manifest = _manifest_path()
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )
    try:
        result = project_gate_runner._run_profile_with_environment(
            "smoke",
            profile,
            "a" * 40,
            manifest,
            REPO_ROOT,
            environment,
            trusted_pnpm=_fake_pnpm_path(environment),
        )
    finally:
        _remove_fake_exports(log_path, manifest)

    assert result == 0
    pnpm = next(call for call in _read_log(log_path) if call["command"] == "pnpm")
    raw_environment_keys = pnpm["environment_keys"]
    assert _is_string_list(raw_environment_keys)
    environment_keys = set(raw_environment_keys)
    for name in (
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "LANGCHAIN_API_KEY",
        "LANGFUSE_SECRET_KEY",
        "NODE_OPTIONS",
        "E2E_LLM_BASE_URL",
        "E2E_LLM_API_KEY",
        "E2E_LLM_MODEL",
    ):
        assert name not in environment_keys
    assert {"PATH", "PNPM_HOME"} <= environment_keys
    assert {"DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT"}.isdisjoint(environment_keys)
    assert pnpm["manifest"] is not None
    assert pnpm["slug"] is not None


def test_runner_allows_live_llm_environment_and_exact_owned_loopback_opt_in(
    tmp_path: Path, project_gate_runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a live profile, when it starts, then only the three LLM vars cross the boundary."""
    expected_argv = [
        "--dir",
        "frontend",
        "test:e2e:live",
        "--",
        "--project=live-manual",
        "e2e/manual/live.spec.ts",
    ]
    log_path, environment = _fake_commands(tmp_path, pnpm_expected_argv=expected_argv)
    environment.update(
        {
            "OPENAI_API_KEY": "sentinel-openai",
            "TAVILY_API_KEY": "sentinel-tavily",
            "LANGCHAIN_API_KEY": "sentinel-langchain",
            "NODE_OPTIONS": "--require=/tmp/sentinel",
            "E2E_LLM_BASE_URL": "https://llm.example.test/v1",
            "E2E_LLM_API_KEY": "live-key",
            "E2E_LLM_MODEL": "live-model",
            "E2E_EGRESS_ALLOW_OWNED_LOOPBACK": "1",
        }
    )
    manifest = _manifest_path()
    profile = project_gate_runner.GateProfile(
        "live", "live-manual", "e2e/manual/live.spec.ts", 1, 0
    )

    try:
        result = project_gate_runner._run_profile_with_environment(
            "live",
            profile,
            "a" * 40,
            manifest,
            REPO_ROOT,
            environment,
            trusted_pnpm=_fake_pnpm_path(environment),
        )
    finally:
        _remove_fake_exports(log_path, manifest)

    assert result == 0
    pnpm = next(call for call in _read_log(log_path) if call["command"] == "pnpm")
    raw_environment_keys = pnpm["environment_keys"]
    assert _is_string_list(raw_environment_keys)
    environment_keys = set(raw_environment_keys)
    assert {
        "E2E_LLM_BASE_URL",
        "E2E_LLM_API_KEY",
        "E2E_LLM_MODEL",
        "E2E_EGRESS_ALLOW_OWNED_LOOPBACK",
    } <= environment_keys
    assert {"OPENAI_API_KEY", "TAVILY_API_KEY", "LANGCHAIN_API_KEY", "NODE_OPTIONS"}.isdisjoint(
        environment_keys
    )
    assert pnpm["manifest"] == str(manifest)
    assert pnpm["slug"] is not None
    assert pnpm["owned_loopback"] == "1"


def test_public_live_gate_preserves_only_live_llm_environment(
    project_gate_runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public live gate keeps its exact live allowlist after toolchain sanitization."""
    manifest = _manifest_path()
    profile = project_gate_runner.GateProfile(
        "live", "live-manual", "e2e/manual/live.spec.ts", 1, 0
    )
    trusted = _direct_gate_toolchain(project_gate_runner, identity_count=1)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        command: list[str], *, env: dict[str, str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, env))
        if command[:3] == ["/trusted/git", "rev-parse", "--verify"]:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40, stderr="")
        if command[:3] == ["/trusted/git", "merge-base", "--is-ancestor"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] != "/trusted/pnpm":
            manifest.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    inherited = {
        "PATH": "/attacker/bin",
        "OPENAI_API_KEY": "sentinel-openai",
        "TAVILY_API_KEY": "sentinel-tavily",
        "LANGCHAIN_API_KEY": "sentinel-langchain",
        "NODE_OPTIONS": "--require=/tmp/sentinel",
        "E2E_LLM_BASE_URL": "https://llm.example.test/v1",
        "E2E_LLM_API_KEY": "live-key",
        "E2E_LLM_MODEL": "live-model",
        "E2E_EGRESS_ALLOW_OWNED_LOOPBACK": "1",
    }
    monkeypatch.setattr(project_gate_runner, "load_profiles", lambda _path: {"live": profile})
    monkeypatch.setattr(project_gate_runner, "resolve_toolchain", lambda _root: (trusted, {}))
    monkeypatch.setattr(project_gate_runner, "verify_toolchain", lambda _toolchain: None)
    monkeypatch.setattr(_runner_subprocess(project_gate_runner), "run", fake_run)

    try:
        result = project_gate_runner.run(
            ["live", "--base-sha", "a" * 40, "--manifest", str(manifest)],
            REPO_ROOT,
            inherited,
        )
    finally:
        manifest.unlink(missing_ok=True)

    assert result == 0
    pnpm_environment = calls[2][1]
    assert {
        "E2E_LLM_BASE_URL",
        "E2E_LLM_API_KEY",
        "E2E_LLM_MODEL",
        "E2E_EGRESS_ALLOW_OWNED_LOOPBACK",
    } <= set(pnpm_environment)
    assert {"OPENAI_API_KEY", "TAVILY_API_KEY", "LANGCHAIN_API_KEY", "NODE_OPTIONS"}.isdisjoint(
        pnpm_environment
    )


def test_runner_checks_direct_manifest_after_successful_child(
    project_gate_runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A green child invokes the checker with the exact direct manifest."""
    manifest = _manifest_path()
    inherited = {
        "PATH": "/usr/bin",
        "HOME": "/tmp/home",
        "OPENAI_API_KEY": "sentinel-openai",
        "TAVILY_API_KEY": "sentinel-tavily",
        "LANGCHAIN_API_KEY": "sentinel-langchain",
        "NODE_OPTIONS": "--require=/tmp/sentinel",
        "PNPM_HOME": "/tmp/pnpm-home",
        "DOCKER_HOST": "tcp://127.0.0.1:65535",
        "DOCKER_CONFIG": "/tmp/attacker-docker-config",
        "DOCKER_CONTEXT": "attacker-context",
        "MOLDY_GATE_DOCKER": "/usr/local/bin/docker",
        "MOLDY_GATE_DOCKER_IDENTITY": "a" * 64,
    }
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        command: list[str], *, env: dict[str, str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, env))
        if command[0] == "/fixed/pnpm":
            return subprocess.CompletedProcess(command, 0)
        manifest.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(_runner_subprocess(project_gate_runner), "run", fake_run)
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )

    try:
        result = project_gate_runner._run_profile_with_environment(
            "smoke",
            profile,
            "a" * 40,
            manifest,
            REPO_ROOT,
            inherited,
            trusted_pnpm=Path("/fixed/pnpm"),
        )
    finally:
        manifest.unlink(missing_ok=True)

    assert result == 0
    assert len(calls) == 2
    pnpm_command, pnpm_environment = calls[0]
    assert pnpm_command == [
        "/fixed/pnpm",
        "--dir",
        "frontend",
        "test:e2e:scripted",
        "--",
        "--project=scripted-smoke",
        "e2e/smoke.spec.ts",
    ]
    assert pnpm_environment["MOLDY_GATE_DOCKER"] == "/usr/local/bin/docker"
    assert pnpm_environment["MOLDY_GATE_DOCKER_IDENTITY"] == "a" * 64
    assert {"DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT"}.isdisjoint(pnpm_environment)
    assert {
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "LANGCHAIN_API_KEY",
        "NODE_OPTIONS",
    }.isdisjoint(pnpm_environment)
    checker_command, checker_environment = calls[1]
    assert checker_command == [
        str(REPO_ROOT / "backend/.venv/bin/python"),
        str(REPO_ROOT / "scripts/check-isolation-cleanup.py"),
        str(manifest),
    ]
    assert checker_environment == {
        "PATH": "/usr/bin",
        "HOME": "/tmp/home",
        "PNPM_HOME": "/tmp/pnpm-home",
        "MOLDY_GATE_DOCKER": "/usr/local/bin/docker",
        "MOLDY_GATE_DOCKER_IDENTITY": "a" * 64,
    }


@pytest.mark.parametrize(
    ("manifest_mode", "checker_exit", "write_manifest"),
    [("failure", 17, True), ("missing", 0, False), ("forged", 1, True)],
)
def test_runner_fails_when_cleanup_checker_rejects_manifest(
    project_gate_runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    manifest_mode: str,
    checker_exit: int,
    write_manifest: bool,
) -> None:
    """Given a successful child, when cleanup validation fails, then the gate remains nonzero."""
    manifest = _manifest_path()
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], *, env: dict[str, str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del env
        calls.append(command)
        if command[0] == "/fixed/pnpm":
            return subprocess.CompletedProcess(command, 0)
        if write_manifest:
            manifest.write_text("{}" if manifest_mode == "forged" else "valid", encoding="utf-8")
        return subprocess.CompletedProcess(command, checker_exit)

    monkeypatch.setattr(_runner_subprocess(project_gate_runner), "run", fake_run)
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )

    try:
        result = project_gate_runner._run_profile_with_environment(
            "smoke",
            profile,
            "a" * 40,
            manifest,
            REPO_ROOT,
            {"PATH": "/usr/bin"},
            trusted_pnpm=Path("/fixed/pnpm"),
        )
    finally:
        manifest.unlink(missing_ok=True)

    assert result == 1
    assert len(calls) == 2
    assert calls[1][-1] == str(manifest)


def test_runner_skips_cleanup_checker_after_child_failure(
    project_gate_runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a failed lifecycle child, when the gate exits, then cleanup checker is not started."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 23)

    monkeypatch.setattr(_runner_subprocess(project_gate_runner), "run", fake_run)
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )

    result = project_gate_runner._run_profile_with_environment(
        "smoke",
        profile,
        "a" * 40,
        _manifest_path(),
        REPO_ROOT,
        {"PATH": "/usr/bin"},
        trusted_pnpm=Path("/fixed/pnpm"),
    )

    assert result == 23
    assert len(calls) == 1


@pytest.mark.parametrize("manifest_mode", ["missing", "forged"])
def test_runner_rejects_missing_or_forged_manifest_with_real_checker(
    tmp_path: Path, project_gate_runner: ModuleType, manifest_mode: str
) -> None:
    """A successful fake lifecycle still fails the independent checker boundary."""
    log_path, environment = _fake_commands(tmp_path, pnpm_manifest_mode=manifest_mode)

    manifest = _manifest_path()
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )
    try:
        result = project_gate_runner._run_profile_with_environment(
            "smoke",
            profile,
            "a" * 40,
            manifest,
            REPO_ROOT,
            environment,
            trusted_pnpm=_fake_pnpm_path(environment),
        )
    finally:
        _remove_fake_exports(log_path, manifest)

    assert result != 0
    assert any(call["command"] == "pnpm" for call in _read_log(log_path))


def test_runner_uses_unique_safe_export_slug_for_reruns(
    tmp_path: Path, project_gate_runner: ModuleType
) -> None:
    """Given same-day reruns, when each gate starts, then export slugs remain distinct and safe."""
    log_path, environment = _fake_commands(tmp_path)
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )

    for _ in range(2):
        manifest = _manifest_path()
        try:
            result = project_gate_runner._run_profile_with_environment(
                "smoke",
                profile,
                "a" * 40,
                manifest,
                REPO_ROOT,
                environment,
                trusted_pnpm=_fake_pnpm_path(environment),
            )
        finally:
            _remove_fake_exports(log_path, manifest)
        assert result == 0

    slugs = [call["slug"] for call in _read_log(log_path) if call["command"] == "pnpm"]
    assert len(slugs) == 2
    assert slugs[0] != slugs[1]
    assert all(
        isinstance(slug, str) and re.fullmatch(r"project-gate-smoke-a{12}-[0-9a-f]{16}", slug)
        for slug in slugs
    )


@pytest.mark.parametrize(
    ("profile", "base", "manifest", "git_mode", "expected"),
    [
        ("missing", "a" * 40, "safe.json", "ok", "unknown_profile"),
        ("smoke", "not-a-commit", "safe.json", "invalid", "base_not_commit"),
        ("smoke", "a" * 39, "safe.json", "invalid", "base_not_commit"),
        ("smoke", "a" * 65, "safe.json", "invalid", "base_not_commit"),
        ("smoke", "A" * 40, "safe.json", "invalid", "base_not_commit"),
        ("smoke", "a" * 40, "../escape.json", "ok", "unsafe_manifest"),
    ],
)
def test_runner_rejects_untrusted_inputs_before_pnpm(
    tmp_path: Path,
    profile: str,
    base: str,
    manifest: str,
    git_mode: str,
    expected: str,
) -> None:
    """Given invalid user input, when run, then pnpm is never invoked."""
    log_path, environment = _fake_commands(tmp_path, git_mode=git_mode)

    safe_manifest = (
        str((_manifest_path()).relative_to(REPO_ROOT)) if manifest == "safe.json" else manifest
    )
    result = _run_wrapper(
        profile,
        "--base-sha",
        base,
        "--manifest",
        safe_manifest,
        environment=environment,
    )

    assert result.returncode == 64
    assert expected in result.stderr
    calls = _read_log(log_path) if log_path.exists() else []
    assert all(call["command"] != "pnpm" for call in calls)


def test_runner_rejects_symlink_and_existing_manifest_before_pnpm(tmp_path: Path) -> None:
    """Given an existing or linked receipt path, when run, then it refuses overwrite/following."""
    log_path, environment = _fake_commands(tmp_path)
    existing = _manifest_path()
    existing.write_text("{}", encoding="utf-8")
    linked = EVIDENCE_ROOT / f"test-project-gate-link-{uuid4().hex}.json"
    linked.symlink_to(existing)
    try:
        for candidate, expected in ((existing, "manifest_exists"), (linked, "unsafe_manifest")):
            result = _run_wrapper(
                "smoke",
                "--base-sha",
                "a" * 40,
                "--manifest",
                str(candidate.relative_to(REPO_ROOT)),
                environment=environment,
            )
            assert result.returncode == 64
            assert expected in result.stderr
    finally:
        linked.unlink(missing_ok=True)
        existing.unlink(missing_ok=True)
    calls = _read_log(log_path) if log_path.exists() else []
    assert all(call["command"] != "pnpm" for call in calls)


def test_runner_rejects_nested_manifest_before_pnpm(tmp_path: Path) -> None:
    """Given a nested manifest path, when selected, then it matches lifecycle boundary rejection."""
    log_path, environment = _fake_commands(tmp_path)
    attempt_dir = EVIDENCE_ROOT / f"test-project-gate-attempt-{uuid4().hex}"
    attempt_dir.mkdir()
    manifest = attempt_dir / "receipt.json"
    try:
        result = _run_wrapper(
            "smoke",
            "--base-sha",
            "a" * 40,
            "--manifest",
            str(manifest.relative_to(REPO_ROOT)),
            environment=environment,
        )
        assert result.returncode == 64
        assert "unsafe_manifest" in result.stderr
    finally:
        attempt_dir.rmdir()
    calls = _read_log(log_path) if log_path.exists() else []
    assert all(call["command"] != "pnpm" for call in calls)


def test_config_parser_rejects_command_injection_value(
    tmp_path: Path, project_gate_runner: ModuleType
) -> None:
    """Given shell-shaped config, when loaded, then it is rejected at the boundary."""
    config = tmp_path / "project-gates.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": {
                    "smoke": {
                        "lane": "scripted",
                        "project": "scripted-smoke; touch pwned",
                        "spec": "e2e/smoke.spec.ts",
                        "workers": 1,
                        "retries": 0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(project_gate_runner.ProjectGateError, match="invalid_config"):
        project_gate_runner.load_profiles(config)


@pytest.mark.parametrize(("field", "value"), [("workers", 2), ("retries", 1)])
def test_config_parser_rejects_noncanonical_execution_counts(
    tmp_path: Path,
    project_gate_runner: ModuleType,
    field: str,
    value: int,
) -> None:
    """Given a profile override, when loaded, then canonical lifecycle counts are required."""
    config = tmp_path / "project-gates.json"
    profile = {
        "lane": "scripted",
        "project": "scripted-smoke",
        "spec": "e2e/smoke.spec.ts",
        "workers": 1,
        "retries": 0,
    }
    profile[field] = value
    config.write_text(
        json.dumps({"schema_version": 1, "profiles": {"smoke": profile}}),
        encoding="utf-8",
    )

    with pytest.raises(project_gate_runner.ProjectGateError, match="invalid_config"):
        project_gate_runner.load_profiles(config)


def test_runner_propagates_pnpm_exit_status(
    tmp_path: Path, project_gate_runner: ModuleType
) -> None:
    """Given a failed lane run, when the gate finishes, then its exit code is preserved."""
    log_path, environment = _fake_commands(tmp_path, pnpm_exit=27)
    manifest = _manifest_path()
    profile = project_gate_runner.GateProfile(
        "scripted", "scripted-smoke", "e2e/smoke.spec.ts", 1, 0
    )
    try:
        result = project_gate_runner._run_profile_with_environment(
            "smoke",
            profile,
            "a" * 40,
            manifest,
            REPO_ROOT,
            environment,
            trusted_pnpm=_fake_pnpm_path(environment),
        )
    finally:
        _remove_fake_exports(log_path, manifest)

    assert result == 27
