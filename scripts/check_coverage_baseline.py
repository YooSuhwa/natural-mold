"""Fail-closed coverage baseline verification for backend and frontend reports."""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 1
MANUAL_POLICY: Final = "reviewed-manual-only"
SHA40: Final = re.compile(r"[0-9a-f]{40}\Z")
BACKEND_KEYS: Final = {
    "schema_version",
    "source_commit",
    "measurement_command",
    "reviewed_runs",
    "metric",
    "covered",
    "total",
    "minimum_percent",
    "update_policy",
}
FRONTEND_KEYS: Final = {
    "schema_version",
    "source_commit",
    "measurement_command",
    "reviewed_runs",
    "metrics",
    "update_policy",
}


class CoverageContractError(RuntimeError):
    """A coverage report or baseline violates the reviewed contract."""


@dataclass(frozen=True, slots=True)
class Metric:
    covered: int
    total: int
    minimum_percent: float

    @property
    def percent(self) -> float:
        return self.covered * 100 / self.total


def _load(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CoverageContractError(f"cannot read coverage JSON: {path}") from error
    if not isinstance(raw, dict):
        raise CoverageContractError("coverage JSON must be an object")
    return raw


def _integer(raw: object, field: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise CoverageContractError(f"{field} must be a non-negative integer")
    return raw


def _number(raw: object, field: str) -> float:
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise CoverageContractError(f"{field} must be numeric")
    value = float(raw)
    if not math.isfinite(value):
        raise CoverageContractError(f"{field} must be finite")
    return value


def _baseline_metric(raw: object, name: str) -> Metric:
    if not isinstance(raw, dict) or set(raw) != {"covered", "total", "minimum_percent"}:
        raise CoverageContractError(f"baseline metric {name} has an invalid schema")
    metric = Metric(
        covered=_integer(raw["covered"], f"{name}.covered"),
        total=_integer(raw["total"], f"{name}.total"),
        minimum_percent=_number(raw["minimum_percent"], f"{name}.minimum_percent"),
    )
    if (
        metric.total == 0
        or metric.covered > metric.total
        or not 0 <= metric.minimum_percent <= 100
        or metric.percent != metric.minimum_percent
    ):
        raise CoverageContractError(f"baseline metric {name} is arbitrary or inconsistent")
    return metric


def _assert_common(
    baseline: dict[str, object], *, keys: set[str], measurement_command: str
) -> None:
    if set(baseline) != keys:
        raise CoverageContractError("coverage baseline keys are invalid")
    if baseline.get("schema_version") != SCHEMA_VERSION:
        raise CoverageContractError("unsupported coverage baseline schema")
    if baseline.get("reviewed_runs") != 2:
        raise CoverageContractError("coverage baseline requires exactly two reviewed runs")
    if baseline.get("update_policy") != MANUAL_POLICY:
        raise CoverageContractError("automatic coverage baseline updates are forbidden")
    source_commit = baseline.get("source_commit")
    if not isinstance(source_commit, str) or SHA40.fullmatch(source_commit) is None:
        raise CoverageContractError("coverage baseline source commit is invalid")
    if baseline.get("measurement_command") != measurement_command:
        raise CoverageContractError("coverage baseline command is invalid")


def baseline_source_commit(path: Path, kind: str) -> str:
    """Return a source commit only after the tracked baseline envelope validates."""
    baseline = _load(path)
    if kind == "backend":
        _assert_common(
            baseline,
            keys=BACKEND_KEYS,
            measurement_command="uv run pytest --cov=app --cov-report=json",
        )
    elif kind == "frontend":
        _assert_common(
            baseline,
            keys=FRONTEND_KEYS,
            measurement_command="pnpm exec vitest --coverage --run",
        )
    else:
        raise CoverageContractError("unsupported coverage baseline kind")
    source_commit = baseline["source_commit"]
    if not isinstance(source_commit, str):
        raise CoverageContractError("coverage baseline source commit is invalid")
    return source_commit


def check_backend(baseline_path: Path, report_path: Path) -> dict[str, Metric]:
    baseline = _load(baseline_path)
    _assert_common(
        baseline,
        keys=BACKEND_KEYS,
        measurement_command="uv run pytest --cov=app --cov-report=json",
    )
    if baseline.get("metric") != "line":
        raise CoverageContractError("backend coverage metric must be line")
    expected = _baseline_metric(
        {
            "covered": baseline.get("covered"),
            "total": baseline.get("total"),
            "minimum_percent": baseline.get("minimum_percent"),
        },
        "line",
    )
    report = _load(report_path)
    totals = report.get("totals")
    if not isinstance(totals, dict):
        raise CoverageContractError("backend report totals are missing")
    actual = Metric(
        covered=_integer(totals.get("covered_lines"), "covered_lines"),
        total=_integer(totals.get("num_statements"), "num_statements"),
        minimum_percent=expected.minimum_percent,
    )
    _assert_no_regression("line", expected, actual)
    return {"line": actual}


def check_frontend(baseline_path: Path, summary_path: Path) -> dict[str, Metric]:
    baseline = _load(baseline_path)
    _assert_common(
        baseline,
        keys=FRONTEND_KEYS,
        measurement_command="pnpm exec vitest --coverage --run",
    )
    expected_raw = baseline.get("metrics")
    summary = _load(summary_path).get("total")
    if not isinstance(expected_raw, dict) or not isinstance(summary, dict):
        raise CoverageContractError("frontend coverage metrics are missing")
    result: dict[str, Metric] = {}
    for name in ("statements", "branches", "functions", "lines"):
        expected = _baseline_metric(expected_raw.get(name), name)
        actual_raw = summary.get(name)
        if not isinstance(actual_raw, dict):
            raise CoverageContractError(f"frontend report metric {name} is missing")
        actual = Metric(
            covered=_integer(actual_raw.get("covered"), f"{name}.covered"),
            total=_integer(actual_raw.get("total"), f"{name}.total"),
            minimum_percent=expected.minimum_percent,
        )
        _assert_no_regression(name, expected, actual)
        result[name] = actual
    return result


def _assert_no_regression(name: str, expected: Metric, actual: Metric) -> None:
    if (
        actual.total == 0
        or actual.covered > actual.total
        or actual.covered * expected.total < expected.covered * actual.total
    ):
        raise CoverageContractError(
            f"{name} coverage regressed "
            f"(actual={actual.covered}/{actual.total}, "
            f"expected={expected.covered}/{expected.total})"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("backend", "frontend"))
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    match args.kind:
        case "backend":
            metrics = check_backend(args.baseline, args.report)
        case "frontend":
            metrics = check_frontend(args.baseline, args.report)
    print(
        json.dumps(
            {
                name: {
                    "covered": metric.covered,
                    "total": metric.total,
                    "percent": metric.percent,
                }
                for name, metric in metrics.items()
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
