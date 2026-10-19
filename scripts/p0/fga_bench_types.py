"""Typed OpenFGA responses and benchmark results."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)


class StoreReply(FrozenModel):
    id: str


class ModelReply(FrozenModel):
    authorization_model_id: str


class CheckReply(FrozenModel):
    allowed: bool
    error: None = None


class BatchReply(FrozenModel):
    result: dict[str, CheckReply]


class ObjectsReply(FrozenModel):
    objects: list[str]


class Summary(FrozenModel):
    n: int
    p50: float
    p95: float
    p99: float
    mean: float
    p95_raw: float = Field(exclude=True)


class AllowedSummary(Summary):
    allowed: int


class ObjectsSummary(Summary):
    avg_results: float
    max_results: int


class BenchResult(FrozenModel):
    check_user_can_run: AllowedSummary
    check_agent_can_use_credential: Summary
    cross_tenant_misgrant_allowed: str
    cross_tenant_allowed_count: int = Field(exclude=True)
    batchcheck_50: Summary
    listobjects_can_run: ObjectsSummary
    listobjects_can_edit: ObjectsSummary
    check_concurrency8: Summary


@dataclass(frozen=True, slots=True)
class LoadedStore:
    store_id: str
    model_id: str
    bad: list[tuple[str, str]]
    tuple_count: int


class StoreJournal(FrozenModel):
    store_ids: list[str]


class TupleCounts(FrozenModel):
    baseline: int
    current: int


class Gate(FrozenModel):
    passed: bool
    failures: list[str]
    check_p95_max_ms: float = 20.0
    batch50_p95_max_ms: float = 60.0


class Report(FrozenModel):
    tuples: TupleCounts
    round1_baseline: BenchResult
    round1_current: BenchResult
    round2_current: BenchResult
    round2_baseline: BenchResult
    acceptance: Gate
    store_ids: list[str]
    stores_deleted: bool


def pct(xs: Sequence[float], p: int) -> float:
    ordered = sorted(xs)
    return ordered[min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))]


def summ(xs: Sequence[float]) -> Summary:
    return Summary(
        n=len(xs),
        p50=round(pct(xs, 50), 2),
        p95=round(pct(xs, 95), 2),
        p99=round(pct(xs, 99), 2),
        mean=round(statistics.mean(xs), 2),
        p95_raw=pct(xs, 95),
    )


def acceptance_failures(result: BenchResult) -> list[str]:
    """Apply the security and latency contract to CURRENT-model measurements only."""
    failures: list[str] = []
    if result.cross_tenant_allowed_count != 0:
        failures.append(f"cross-tenant grants: {result.cross_tenant_misgrant_allowed}")
    for name, summary in (
        ("check_user_can_run", result.check_user_can_run),
        ("check_agent_can_use_credential", result.check_agent_can_use_credential),
        ("check_concurrency8", result.check_concurrency8),
    ):
        if summary.p95_raw > 20:
            failures.append(f"{name} p95 {summary.p95_raw:.4f}ms exceeds 20ms")
    if result.batchcheck_50.p95_raw > 60:
        failures.append(f"batchcheck_50 p95 {result.batchcheck_50.p95_raw:.4f}ms exceeds 60ms")
    return failures
