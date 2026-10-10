#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["anyio>=4", "httpx>=0.27", "pydantic>=2"]
# ///
# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: OPENFGA_URL=http://127.0.0.1:18095 uv run scripts/p0/fga_bench.py
# 3. Optional FGA_BENCH_OUTPUT_DIR overrides the unique ignored output directory.
# ──────────────────
"""A-12 artificial workload benchmark; exit 1 when CURRENT fails its contract.

Tenants 10, organizations 50, users 5,000, agents 20,000; baseline/current tuples
131,309/151,309 with 100 cross-tenant misgrants. Two rounds reverse model order.
Only dedicated disposable OpenFGA lab stores are created, then deleted.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import uuid4

import anyio
import httpx
from fga_bench_client import require_status, setup
from fga_bench_data import tuples  # noqa: F401 - compatibility and regression-test entry point
from fga_bench_measure import bench
from fga_bench_types import (
    BenchResult,
    Gate,
    Report,
    StoreJournal,
    TupleCounts,
    acceptance_failures,
)

HERE: Final = Path(__file__).resolve().parent
REPO: Final = HERE.parent.parent
MODELS: Final = HERE / "models"


async def run_benchmark(base_url: str, output_dir: Path) -> int:
    """Record full results and delete only stores created by this invocation."""
    await anyio.Path(output_dir).mkdir(parents=True, exist_ok=True)
    canonical = await anyio.Path(REPO / "backend/app/authz/model.json").read_bytes()
    current_bytes = await anyio.Path(MODELS / "model_current.json").read_bytes()
    if canonical != current_bytes:
        message = "Benchmark current JSON must match canonical backend/app/authz/model.json exactly"
        raise RuntimeError(message)
    store_ids: list[str] = []
    results: dict[str, BenchResult] = {}
    failures: list[str] = []
    timeout = httpx.Timeout(30, connect=5, pool=10)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        try:
            baseline = await setup(
                client,
                "bench-baseline",
                MODELS / "model_baseline.json",
                False,
                store_ids,
                output_dir / "stores.json",
            )
            current = await setup(
                client,
                "bench-current",
                MODELS / "model_current.json",
                True,
                store_ids,
                output_dir / "stores.json",
            )
            if (baseline.tuple_count, current.tuple_count) != (131309, 151309):
                message = "Artificial tuple workload count changed"
                raise RuntimeError(message)
            for round_number, order in enumerate(
                (
                    (("baseline", baseline), ("current", current)),
                    (("current", current), ("baseline", baseline)),
                ),
                start=1,
            ):
                for name, store in order:
                    result = await bench(client, store.store_id, store.model_id, store.bad)
                    results[f"round{round_number}_{name}"] = result
                    if name == "current":
                        failures.extend(
                            f"round{round_number}: {item}" for item in acceptance_failures(result)
                        )
                    print(name, round_number, result.model_dump_json(), flush=True)
        finally:
            # Persistent journal permits cleanup even if a delete fails or the run is interrupted.
            await anyio.Path(output_dir / "stores.json").write_text(
                StoreJournal(store_ids=store_ids).model_dump_json(indent=2),
                encoding="utf-8",
            )
            with anyio.CancelScope(shield=True):
                for store_id in store_ids:
                    response = await client.delete(f"/stores/{store_id}")
                    require_status(response, 204)
                    print(f"deleted store {store_id}", flush=True)
    report = Report(
        tuples=TupleCounts(baseline=baseline.tuple_count, current=current.tuple_count),
        round1_baseline=results["round1_baseline"],
        round1_current=results["round1_current"],
        round2_current=results["round2_current"],
        round2_baseline=results["round2_baseline"],
        acceptance=Gate(passed=not failures, failures=failures),
        store_ids=store_ids,
        stores_deleted=True,
    )
    await anyio.Path(output_dir / "fga_bench_results.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"CURRENT acceptance: {'PASS' if not failures else 'FAIL'}; output={output_dir}", flush=True
    )
    return int(bool(failures))


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    default = REPO / "output/org-authz/p0" / f"bench-refined-{stamp}-{uuid4().hex[:8]}"
    output_dir = Path(os.environ.get("FGA_BENCH_OUTPUT_DIR", str(default))).resolve()
    return anyio.run(
        run_benchmark, os.environ.get("OPENFGA_URL", "http://127.0.0.1:18091"), output_dir
    )


if __name__ == "__main__":
    raise SystemExit(main())
