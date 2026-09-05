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


def run_gate(kind: str, repo_root: Path) -> None:
    baseline = repo_root / kind / "quality/coverage-baseline.json"
    source_commit = baseline_source_commit(baseline, kind)
    provenance = resolve_provenance(source_commit, repo_root)
    with tempfile.TemporaryDirectory(prefix=f"moldy-{kind}-coverage-") as temporary:
        report_root = Path(temporary)
        if kind == "backend":
            report = report_root / "coverage.json"
            uv = shutil.which("uv")
            if uv is None:
                raise RuntimeError("uv is required for backend coverage")
            _run_measurement(
                [uv, "run", "pytest", "--cov=app", f"--cov-report=json:{report}"],
                cwd=repo_root / "backend",
            )
        else:
            report = report_root / "coverage-summary.json"
            pnpm = shutil.which("pnpm")
            if pnpm is None:
                raise RuntimeError("pnpm is required for frontend coverage")
            env = dict(os.environ)
            env[FRONTEND_COVERAGE_DIRECTORY] = str(report_root)
            _run_measurement(
                [pnpm, "--dir", str(repo_root / "frontend"), "test:coverage", "--", "--run"],
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
