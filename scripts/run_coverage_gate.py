"""Measure coverage in one isolated workspace and enforce the reviewed floor."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from check_coverage_baseline import (
    CoverageContractError,
    baseline_source_commit,
    check_backend,
    check_frontend,
)
from project_gate_toolchain import resolve_provenance, verify_provenance

FRONTEND_COVERAGE_DIRECTORY = "MOLDY_VITEST_COVERAGE_DIRECTORY"
GATE_UV_ENVIRONMENT_NAME = "MOLDY_GATE_UV"
ISOLATED_RUN_ROOT_ENVIRONMENT_NAME = "MOLDY_TEST_RUN_ROOT"
ISOLATED_RUN_ROOT_PREFIX = ".moldy-test-run."


def _run_measurement(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(argv, cwd=cwd, env=env, check=True)  # noqa: S603 - reviewed argv


def _require_fresh_report(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise CoverageContractError("coverage measurement did not create a fresh report") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_size == 0
    ):
        raise CoverageContractError("coverage measurement did not create a fresh report")


def _backend_uv() -> str:
    """Return the reviewed gate uv path, or PATH lookup for standalone execution."""
    configured = os.environ.get(GATE_UV_ENVIRONMENT_NAME)
    if configured is None:
        fallback = shutil.which("uv")
        if fallback is None:
            raise RuntimeError("uv is required for backend coverage")
        return fallback
    uv = Path(configured)
    try:
        metadata = uv.lstat()
    except OSError as error:
        raise RuntimeError("MOLDY_GATE_UV must name an executable absolute path") from error
    unsafe_mode = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if (
        not uv.is_absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or unsafe_mode
        or not os.access(uv, os.X_OK)
    ):
        raise RuntimeError("MOLDY_GATE_UV must name an executable absolute path")
    return str(uv)


def _coverage_temp_parent() -> Path | None:
    """Bind coverage artifacts to the wrapper-owned root when one is active."""
    configured = os.environ.get(ISOLATED_RUN_ROOT_ENVIRONMENT_NAME)
    if configured is None:
        return None
    root = Path(configured)
    try:
        metadata = root.lstat()
        canonical = root.resolve(strict=True)
        active_temp = Path(os.environ.get("TMPDIR", tempfile.gettempdir())).resolve(strict=True)
        parent = root.parent.resolve(strict=True)
    except OSError as error:
        raise CoverageContractError("isolated coverage root is invalid") from error
    system_temp = (
        canonical.parent
        if active_temp == canonical or canonical in active_temp.parents
        else active_temp
    )
    if (
        not root.is_absolute()
        or parent != system_temp
        or canonical.parent != system_temp
        or canonical.name != root.name
        or not root.name.startswith(ISOLATED_RUN_ROOT_PREFIX)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        raise CoverageContractError("isolated coverage root is invalid")
    return canonical


def run_gate(kind: str, repo_root: Path) -> None:
    baseline = repo_root / kind / "quality/coverage-baseline.json"
    source_commit = baseline_source_commit(baseline, kind)
    provenance = resolve_provenance(source_commit, repo_root)
    with tempfile.TemporaryDirectory(
        prefix=f"moldy-{kind}-coverage-", dir=_coverage_temp_parent()
    ) as temporary:
        report_root = Path(temporary)
        if kind == "backend":
            report = report_root / "coverage.json"
            pytest_temp_root = report_root / "tmp"
            pytest_temp_root.mkdir(mode=0o700)
            environment = dict(os.environ)
            environment["TMPDIR"] = str(pytest_temp_root)
            uv = _backend_uv()
            _run_measurement(
                [uv, "run", "pytest", "--cov=app", f"--cov-report=json:{report}"],
                cwd=repo_root / "backend",
                env=environment,
            )
        else:
            report = report_root / "coverage-summary.json"
            pnpm = shutil.which("pnpm")
            if pnpm is None:
                raise RuntimeError("pnpm is required for frontend coverage")
            env = dict(os.environ)
            env[FRONTEND_COVERAGE_DIRECTORY] = str(report_root)
            _run_measurement(
                [
                    pnpm,
                    "--dir",
                    str(repo_root / "frontend"),
                    "exec",
                    "vitest",
                    "--coverage",
                    "--run",
                ],
                cwd=repo_root,
                env=env,
            )
        verify_provenance(provenance, repo_root)
        _require_fresh_report(report)
        verify_provenance(provenance, repo_root)
        if kind == "backend":
            check_backend(baseline, report)
        else:
            check_frontend(baseline, report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("backend", "frontend"))
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    run_gate(args.kind, repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
