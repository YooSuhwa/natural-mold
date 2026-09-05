from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import run_coverage_gate  # noqa: E402
from check_coverage_baseline import (  # noqa: E402
    CoverageContractError,
    check_backend,
    check_frontend,
)
from project_gate_runtime import ProjectGateError  # noqa: E402


def _write(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_backend_coverage_accepts_reviewed_exact_counts(tmp_path: Path) -> None:
    baseline = _write(
        tmp_path / "baseline.json",
        {
            "schema_version": 1,
            "source_commit": "a" * 40,
            "reviewed_runs": 2,
            "update_policy": "reviewed-manual-only",
            "measurement_command": "uv run pytest --cov=app --cov-report=json",
            "metric": "line",
            "covered": 80,
            "total": 100,
            "minimum_percent": 80.0,
        },
    )
    report = _write(
        tmp_path / "report.json", {"totals": {"covered_lines": 80, "num_statements": 100}}
    )

    assert check_backend(baseline, report)["line"].covered == 80


def test_backend_coverage_rejects_ratio_regression(tmp_path: Path) -> None:
    baseline = _write(
        tmp_path / "baseline.json",
        {
            "schema_version": 1,
            "source_commit": "a" * 40,
            "reviewed_runs": 2,
            "update_policy": "reviewed-manual-only",
            "measurement_command": "uv run pytest --cov=app --cov-report=json",
            "metric": "line",
            "covered": 80,
            "total": 100,
            "minimum_percent": 80.0,
        },
    )
    report = _write(
        tmp_path / "report.json", {"totals": {"covered_lines": 159, "num_statements": 200}}
    )

    with pytest.raises(CoverageContractError):
        check_backend(baseline, report)


def test_backend_coverage_allows_new_denominator_at_same_ratio(tmp_path: Path) -> None:
    baseline = _write(
        tmp_path / "baseline.json",
        {
            "schema_version": 1,
            "source_commit": "a" * 40,
            "measurement_command": "uv run pytest --cov=app --cov-report=json",
            "reviewed_runs": 2,
            "metric": "line",
            "update_policy": "reviewed-manual-only",
            "covered": 80,
            "total": 100,
            "minimum_percent": 80.0,
        },
    )
    report = _write(
        tmp_path / "report.json", {"totals": {"covered_lines": 160, "num_statements": 200}}
    )

    assert check_backend(baseline, report)["line"].percent == 80.0


@pytest.mark.parametrize(
    ("covered", "total"),
    [(101, 100), (-1, 100), (1, -1), (1, 0)],
)
def test_backend_coverage_rejects_impossible_report_counts(
    tmp_path: Path, covered: int, total: int
) -> None:
    baseline = _write(
        tmp_path / "baseline.json",
        {
            "schema_version": 1,
            "source_commit": "a" * 40,
            "measurement_command": "uv run pytest --cov=app --cov-report=json",
            "reviewed_runs": 2,
            "metric": "line",
            "update_policy": "reviewed-manual-only",
            "covered": 80,
            "total": 100,
            "minimum_percent": 80.0,
        },
    )
    report = _write(
        tmp_path / "report.json",
        {"totals": {"covered_lines": covered, "num_statements": total}},
    )

    with pytest.raises(CoverageContractError):
        check_backend(baseline, report)


@pytest.mark.parametrize(
    ("policy", "minimum"),
    [("automatic", 80.0), ("reviewed-manual-only", 75.0)],
)
def test_coverage_rejects_auto_update_or_arbitrary_floor(
    tmp_path: Path, policy: str, minimum: float
) -> None:
    baseline = _write(
        tmp_path / "baseline.json",
        {
            "schema_version": 1,
            "source_commit": "a" * 40,
            "reviewed_runs": 2,
            "update_policy": policy,
            "measurement_command": "uv run pytest --cov=app --cov-report=json",
            "metric": "line",
            "covered": 80,
            "total": 100,
            "minimum_percent": minimum,
        },
    )
    report = _write(
        tmp_path / "report.json", {"totals": {"covered_lines": 80, "num_statements": 100}}
    )

    with pytest.raises(CoverageContractError):
        check_backend(baseline, report)


def test_frontend_coverage_rejects_one_regressed_metric(tmp_path: Path) -> None:
    metric = {"covered": 80, "total": 100, "minimum_percent": 80.0}
    baseline = _write(
        tmp_path / "baseline.json",
        {
            "schema_version": 1,
            "source_commit": "a" * 40,
            "reviewed_runs": 2,
            "update_policy": "reviewed-manual-only",
            "measurement_command": "pnpm test:coverage -- --run",
            "metrics": dict.fromkeys(("statements", "branches", "functions", "lines"), metric),
        },
    )
    report_metrics = {
        name: {"covered": 80, "total": 100}
        for name in ("statements", "branches", "functions", "lines")
    }
    report_metrics["branches"] = {"covered": 79, "total": 100}
    report = _write(tmp_path / "summary.json", {"total": report_metrics})

    with pytest.raises(CoverageContractError, match="branches coverage regressed"):
        check_frontend(baseline, report)


def test_tracked_frontend_baseline_accepts_reviewed_exact_counts(tmp_path: Path) -> None:
    baseline = Path(__file__).parents[2] / "frontend/quality/coverage-baseline.json"
    report = _write(
        tmp_path / "coverage-summary.json",
        {
            "total": {
                "statements": {"covered": 11801, "total": 18860},
                "branches": {"covered": 8595, "total": 15594},
                "functions": {"covered": 3146, "total": 5668},
                "lines": {"covered": 10659, "total": 16553},
            }
        },
    )

    result = check_frontend(baseline, report)

    assert result["branches"].covered == 8595
    assert result["lines"].total == 16553


@pytest.mark.parametrize("mutation", ["stale", "impossible"])
def test_tracked_frontend_baseline_rejects_stale_or_impossible_counts(
    tmp_path: Path, mutation: str
) -> None:
    tracked = Path(__file__).parents[2] / "frontend/quality/coverage-baseline.json"
    payload = json.loads(tracked.read_text(encoding="utf-8"))
    if mutation == "stale":
        payload["metrics"]["branches"]["covered"] = 8594
    else:
        payload["metrics"]["branches"]["covered"] = 16000
    baseline = _write(tmp_path / "baseline.json", payload)
    report = _write(
        tmp_path / "coverage-summary.json",
        {
            "total": {
                name: {"covered": metric["covered"], "total": metric["total"]}
                for name, metric in payload["metrics"].items()
            }
        },
    )

    with pytest.raises(CoverageContractError, match="arbitrary or inconsistent"):
        check_frontend(baseline, report)


def test_coverage_cli_emits_machine_readable_counts(tmp_path: Path) -> None:
    baseline = _write(
        tmp_path / "baseline.json",
        {
            "schema_version": 1,
            "source_commit": "a" * 40,
            "measurement_command": "uv run pytest --cov=app --cov-report=json",
            "reviewed_runs": 2,
            "metric": "line",
            "update_policy": "reviewed-manual-only",
            "covered": 80,
            "total": 100,
            "minimum_percent": 80.0,
        },
    )
    report = _write(
        tmp_path / "report.json", {"totals": {"covered_lines": 80, "num_statements": 100}}
    )

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[2] / "scripts/check_coverage_baseline.py"),
            "backend",
            "--baseline",
            str(baseline),
            "--report",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["line"] == {"covered": 80, "total": 100, "percent": 80.0}


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Coverage Test",
            "GIT_AUTHOR_EMAIL": "coverage@example.invalid",
            "GIT_COMMITTER_NAME": "Coverage Test",
            "GIT_COMMITTER_EMAIL": "coverage@example.invalid",
        },
    )
    return result.stdout.strip()


def _coverage_repo(tmp_path: Path, kind: str, *, source: str | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "initial")
    ancestor = _git(repo, "rev-parse", "HEAD")
    baseline_dir = repo / kind / "quality"
    baseline_dir.mkdir(parents=True)
    if kind == "backend":
        baseline: dict[str, object] = {
            "schema_version": 1,
            "source_commit": source or ancestor,
            "measurement_command": "uv run pytest --cov=app --cov-report=json",
            "reviewed_runs": 2,
            "metric": "line",
            "covered": 80,
            "total": 100,
            "minimum_percent": 80.0,
            "update_policy": "reviewed-manual-only",
        }
    else:
        metric = {"covered": 80, "total": 100, "minimum_percent": 80.0}
        baseline = {
            "schema_version": 1,
            "source_commit": source or ancestor,
            "measurement_command": "pnpm test:coverage -- --run",
            "reviewed_runs": 2,
            "metrics": {
                name: dict(metric) for name in ("statements", "branches", "functions", "lines")
            },
            "update_policy": "reviewed-manual-only",
        }
    _write(baseline_dir / "coverage-baseline.json", baseline)
    _git(repo, "add", f"{kind}/quality/coverage-baseline.json")
    _git(repo, "commit", "-qm", "add baseline")
    return repo, ancestor


def _write_measurement_report(kind: str, argv: list[str], env: dict[str, str] | None) -> Path:
    if kind == "backend":
        report = Path(
            next(value.split(":", 1)[1] for value in argv if value.startswith("--cov-report"))
        )
        _write(report, {"totals": {"covered_lines": 80, "num_statements": 100}})
    else:
        assert env is not None
        report = Path(env[run_coverage_gate.FRONTEND_COVERAGE_DIRECTORY]) / "coverage-summary.json"
        _write(
            report,
            {
                "total": {
                    name: {"covered": 80, "total": 100}
                    for name in ("statements", "branches", "functions", "lines")
                }
            },
        )
    return report


@pytest.mark.parametrize("kind", ["backend", "frontend"])
def test_coverage_runner_accepts_older_ancestor_and_fresh_external_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    repo, ancestor = _coverage_repo(tmp_path, kind)
    calls: list[tuple[list[str], Path, dict[str, str] | None, Path]] = []

    def measure(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        report = _write_measurement_report(kind, argv, env)
        calls.append((argv, cwd, env, report))

    monkeypatch.setattr(run_coverage_gate.shutil, "which", lambda command: f"/fixture/{command}")
    monkeypatch.setattr(run_coverage_gate, "_run_measurement", measure)

    run_coverage_gate.run_gate(kind, repo)

    argv, cwd, env, report = calls[0]
    assert _git(repo, "merge-base", "--is-ancestor", ancestor, "HEAD") == ""
    assert not report.is_relative_to(repo)
    assert not report.exists()
    if kind == "backend":
        assert argv[:4] == ["/fixture/uv", "run", "pytest", "--cov=app"]
        assert cwd == repo / "backend"
        assert env is None
        assert not (repo / "backend/coverage.json").exists()
    else:
        assert argv == [
            "/fixture/pnpm",
            "--dir",
            str(repo / "frontend"),
            "test:coverage",
            "--",
            "--run",
        ]
        assert cwd == repo
        assert (
            env is not None
            and Path(env[run_coverage_gate.FRONTEND_COVERAGE_DIRECTORY]).is_absolute()
        )
        assert not (repo / "frontend/coverage").exists()


def test_coverage_runner_rejects_nonexistent_source_commit(tmp_path: Path) -> None:
    repo, _ = _coverage_repo(tmp_path, "backend", source="f" * 40)

    with pytest.raises(ProjectGateError, match="base_not_commit"):
        run_coverage_gate.run_gate("backend", repo)


def test_coverage_runner_rejects_nonancestor_source_commit(tmp_path: Path) -> None:
    repo, _ = _coverage_repo(tmp_path, "backend")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    orphan = _git(repo, "commit-tree", tree, "-m", "orphan")
    baseline = repo / "backend/quality/coverage-baseline.json"
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["source_commit"] = orphan
    _write(baseline, payload)
    _git(repo, "add", "backend/quality/coverage-baseline.json")
    _git(repo, "commit", "-qm", "point at nonancestor")

    with pytest.raises(ProjectGateError, match="base_not_ancestor"):
        run_coverage_gate.run_gate("backend", repo)


@pytest.mark.parametrize("dirty", ["tracked", "untracked"])
def test_coverage_runner_rejects_dirty_repository(tmp_path: Path, dirty: str) -> None:
    repo, _ = _coverage_repo(tmp_path, "backend")
    target = repo / ("tracked.txt" if dirty == "tracked" else "untracked.txt")
    target.write_text("changed\n", encoding="utf-8")

    with pytest.raises(ProjectGateError, match="repository_changed"):
        run_coverage_gate.run_gate("backend", repo)


@pytest.mark.parametrize("mutation", ["head", "worktree"])
def test_coverage_runner_rejects_repository_change_during_measurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    repo, _ = _coverage_repo(tmp_path, "backend")

    def measure(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        _write_measurement_report("backend", argv, env)
        if mutation == "head":
            _git(repo, "commit", "--allow-empty", "-qm", "measurement mutation")
        else:
            (repo / "measurement-residue.txt").write_text("residue\n", encoding="utf-8")

    monkeypatch.setattr(run_coverage_gate.shutil, "which", lambda _: "/fixture/uv")
    monkeypatch.setattr(run_coverage_gate, "_run_measurement", measure)

    with pytest.raises(ProjectGateError, match="repository_changed"):
        run_coverage_gate.run_gate("backend", repo)


def test_coverage_runner_rejects_missing_fresh_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, _ = _coverage_repo(tmp_path, "backend")
    monkeypatch.setattr(run_coverage_gate.shutil, "which", lambda _: "/fixture/uv")
    monkeypatch.setattr(run_coverage_gate, "_run_measurement", lambda *_args, **_kwargs: None)

    with pytest.raises(CoverageContractError, match="fresh report"):
        run_coverage_gate.run_gate("backend", repo)
