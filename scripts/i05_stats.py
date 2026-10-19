"""Latency percentiles and target evaluation for the I-05 load harness (plan 8.10, 16.6)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal

Verdict = Literal["pass", "fail", "measured", "skipped"]


def percentile(samples: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (numpy's default ``linear`` method)."""
    if not samples:
        raise ValueError("percentile of an empty sample")
    if not 0 <= q <= 100:
        raise ValueError(f"percentile {q} outside 0..100")
    ordered = sorted(samples)
    rank = (len(ordered) - 1) * q / 100
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


@dataclass(frozen=True, slots=True)
class Target:
    """Pass/fail bound for one metric; ``None`` latency bounds mean "measure only"."""

    p95_ms: float | None = None
    p99_ms: float | None = None
    max_error_rate: float = 0.0
    source: str = ""


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    samples: int
    errors: int
    error_rate: float
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    max_ms: float | None
    target: Target
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    error_examples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _bound(label: str, value: float | None, limit: float | None) -> str | None:
    if limit is None or value is None or value <= limit:
        return None
    return f"{label} {value:.1f}ms > target {limit:.1f}ms"


def evaluate(
    name: str,
    latencies_ms: Sequence[float],
    errors: int,
    target: Target,
    error_examples: Sequence[str] = (),
) -> MetricResult:
    """Summarise samples and decide the verdict against ``target``."""
    total = len(latencies_ms) + errors
    error_rate = errors / total if total else 0.0
    stats: dict[str, float | None] = {"p50": None, "p95": None, "p99": None, "max": None}
    if latencies_ms:
        stats = {
            "p50": percentile(latencies_ms, 50),
            "p95": percentile(latencies_ms, 95),
            "p99": percentile(latencies_ms, 99),
            "max": max(latencies_ms),
        }
    reasons: list[str] = []
    if not latencies_ms:
        reasons.append("no successful samples")
    if error_rate > target.max_error_rate:
        reasons.append(f"error rate {error_rate:.2%} > allowed {target.max_error_rate:.2%}")
    for label, key, limit in (("p95", "p95", target.p95_ms), ("p99", "p99", target.p99_ms)):
        reason = _bound(label, stats[key], limit)
        if reason:
            reasons.append(reason)
    has_bound = target.p95_ms is not None or target.p99_ms is not None
    verdict: Verdict = "fail" if reasons else ("pass" if has_bound else "measured")
    return MetricResult(
        name=name,
        samples=len(latencies_ms),
        errors=errors,
        error_rate=error_rate,
        p50_ms=stats["p50"],
        p95_ms=stats["p95"],
        p99_ms=stats["p99"],
        max_ms=stats["max"],
        target=target,
        verdict=verdict,
        reasons=reasons,
        error_examples=list(error_examples)[:5],
    )


def skipped(name: str, reason: str, target: Target) -> MetricResult:
    return MetricResult(name, 0, 0, 0.0, None, None, None, None, target, "skipped", [reason])


def overall_failed(results: Sequence[MetricResult]) -> bool:
    return any(result.verdict == "fail" for result in results)
