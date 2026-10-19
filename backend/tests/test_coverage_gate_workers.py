"""The backend coverage gate measures the parallel CI suite in its one run."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import run_coverage_gate  # noqa: E402
from check_coverage_baseline import CoverageContractError  # noqa: E402

from tests.test_coverage_governance import (  # noqa: E402
    _coverage_repo,
    _write_measurement_report,
)


def test_backend_coverage_runner_measures_suite_with_xdist_workers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given CI's worker count, the one measured run is the parallel suite itself."""
    repo, _ = _coverage_repo(tmp_path, "backend")
    measured: list[tuple[list[str], dict[str, str] | None]] = []

    def measure(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        _write_measurement_report("backend", argv, env)
        measured.append((argv, env))

    monkeypatch.delenv(run_coverage_gate.GATE_UV_ENVIRONMENT_NAME, raising=False)
    monkeypatch.setattr(run_coverage_gate.shutil, "which", lambda _command: "/fixture/uv")
    monkeypatch.setattr(run_coverage_gate, "_run_measurement", measure)

    run_coverage_gate.run_gate("backend", repo, workers=4)

    assert len(measured) == 1
    argv, env = measured[0]
    assert argv[:6] == ["/fixture/uv", "run", "pytest", "-n", "4", "--cov=app"]
    assert env is not None and Path(env["COVERAGE_FILE"]).parent == Path(env["TMPDIR"]).parent


@pytest.mark.parametrize(("kind", "workers"), [("frontend", 4), ("backend", 0)])
def test_coverage_runner_rejects_invalid_worker_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, workers: int
) -> None:
    repo, _ = _coverage_repo(tmp_path, kind)
    monkeypatch.setattr(run_coverage_gate, "_run_measurement", pytest.fail)

    with pytest.raises(CoverageContractError, match="workers"):
        run_coverage_gate.run_gate(kind, repo, workers=workers)
