"""Concurrency/ramp engine for the I-05 load scenarios."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from i05_load_scenarios import LoadContext, Scenario
from i05_stats import MetricResult, Target, evaluate, skipped


@dataclass(frozen=True, slots=True)
class LoadSettings:
    concurrency: int = 8
    iterations: int = 200
    warmup: int = 5
    ramp_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.concurrency < 1 or self.iterations < 1 or self.warmup < 0:
            raise ValueError("concurrency and iterations must be >= 1, warmup >= 0")
        if self.ramp_seconds < 0:
            raise ValueError("ramp_seconds must be >= 0")


def lookup(sections: Mapping[str, Any], dotted: str) -> Any:
    node: Any = sections
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def missing_needs(scenario: Scenario, sections: Mapping[str, Any]) -> list[str]:
    return [need for need in scenario.needs if lookup(sections, need) in (None, "", [], {})]


def select(
    catalog: Sequence[Scenario], requested: Sequence[str] | None
) -> tuple[list[Scenario], list[str]]:
    """Requested names (all non-opt-in when empty) and unknown names."""
    by_name = {scenario.name: scenario for scenario in catalog}
    if not requested:
        return [s for s in catalog if not s.opt_in], []
    unknown = [name for name in requested if name not in by_name]
    return [by_name[name] for name in requested if name in by_name], unknown


def apply_overrides(scenario: Scenario, overrides: Mapping[str, Any]) -> dict[str, Target]:
    targets = dict(scenario.targets)
    for metric, base in scenario.targets.items():
        override = overrides.get(metric)
        if isinstance(override, Mapping):
            targets[metric] = Target(
                p95_ms=override.get("p95_ms", base.p95_ms),
                p99_ms=override.get("p99_ms", base.p99_ms),
                max_error_rate=float(override.get("max_error_rate", base.max_error_rate)),
                source=str(override.get("source", "config override")),
            )
    return targets


async def run_scenario(
    scenario: Scenario,
    ctx: LoadContext,
    settings: LoadSettings,
    targets: Mapping[str, Target],
) -> list[MetricResult]:
    """Warm up, then run ``iterations`` ops over ramped workers; one result per metric."""
    for _ in range(settings.warmup):
        try:
            await scenario.op(ctx)
        except Exception:  # noqa: BLE001, S112 - warm-up failures are re-measured below
            continue
    samples: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    examples: dict[str, list[str]] = defaultdict(list)
    remaining = settings.iterations
    workers = min(scenario.concurrency or settings.concurrency, settings.iterations)

    def fail(exc: BaseException) -> None:
        for metric in targets:
            errors[metric] += 1
            if len(examples[metric]) < 5:
                examples[metric].append(f"{type(exc).__name__}: {exc}"[:300])

    async def worker(index: int) -> None:
        nonlocal remaining
        if settings.ramp_seconds and workers > 1:
            await asyncio.sleep(settings.ramp_seconds * index / workers)
        while remaining > 0:
            remaining -= 1
            started = time.perf_counter()
            try:
                measured = await scenario.op(ctx)
            except Exception as exc:  # noqa: BLE001 - every failure is an error sample
                fail(exc)
                continue
            if measured is None:
                samples[scenario.name].append((time.perf_counter() - started) * 1000)
            else:
                for metric, value in measured.items():
                    samples[metric].append(value)

    await asyncio.gather(*(worker(index) for index in range(workers)))
    return [
        evaluate(metric, samples[metric], errors[metric], target, examples[metric])
        for metric, target in targets.items()
    ]


def skip_all(scenario: Scenario, reason: str, targets: Mapping[str, Target]) -> list[MetricResult]:
    return [skipped(metric, reason, target) for metric, target in targets.items()]
