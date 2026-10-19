"""The supplied sequential and eight-worker measurement schedule."""

from __future__ import annotations

import random
import statistics

import anyio
import httpx
from fga_bench_client import batch_check, check, list_objects
from fga_bench_data import agents, creds, orgs, users
from fga_bench_types import AllowedSummary, BenchResult, ObjectsSummary, summ


async def bench(
    client: httpx.AsyncClient,
    store_id: str,
    model_id: str,
    bad: list[tuple[str, str]],
) -> BenchResult:
    rnd = random.Random(99)  # noqa: S311 - identical supplied artificial query sequence

    async def user_check() -> tuple[float, bool]:
        org = rnd.choice(orgs)
        return await check(
            client,
            store_id,
            model_id,
            f"user:{rnd.choice(users[org])}",
            "can_run",
            f"agent:{rnd.choice(agents[org])}",
        )

    for _ in range(100):
        await user_check()
    latencies: list[float] = []
    allowed = 0
    for _ in range(1000):
        elapsed, ok = await user_check()
        latencies.append(elapsed)
        allowed += ok
    user_summary = AllowedSummary(
        **summ(latencies).model_dump(), p95_raw=summ(latencies).p95_raw, allowed=allowed
    )

    latencies = []
    for _ in range(500):
        org = rnd.choice(orgs)
        credential = rnd.choice(creds[org])
        agent = rnd.choice(agents[org])
        elapsed, _ = await check(
            client,
            store_id,
            model_id,
            f"agent:{agent}",
            "can_use",
            f"credential:{credential}",
        )
        latencies.append(elapsed)
    credential_summary = summ(latencies)

    cross_allowed = 0
    for user, agent in bad:
        _, ok = await check(client, store_id, model_id, f"user:{user}", "can_run", f"agent:{agent}")
        cross_allowed += ok

    latencies = []
    for _ in range(200):
        org = rnd.choice(orgs)
        user = rnd.choice(users[org])
        latencies.append(
            await batch_check(
                client,
                store_id,
                model_id,
                f"user:{user}",
                rnd.sample(agents[org], 50),
            )
        )
    batch_summary = summ(latencies)

    async def measure_objects(relation: str) -> ObjectsSummary:
        latencies: list[float] = []
        sizes: list[int] = []
        for _ in range(100):
            org = rnd.choice(orgs)
            user = rnd.choice(users[org])
            elapsed, size = await list_objects(client, store_id, model_id, f"user:{user}", relation)
            latencies.append(elapsed)
            sizes.append(size)
        summary = summ(latencies)
        return ObjectsSummary(
            **summary.model_dump(),
            p95_raw=summary.p95_raw,
            avg_results=round(statistics.mean(sizes), 1),
            max_results=max(sizes),
        )

    list_run = await measure_objects("can_run")
    list_edit = await measure_objects("can_edit")
    parts: list[list[float]] = [[] for _ in range(8)]

    async def burst(index: int) -> None:
        for _ in range(250):
            elapsed, _ = await user_check()
            parts[index].append(elapsed)

    async with anyio.create_task_group() as tasks:
        for index in range(8):
            tasks.start_soon(burst, index)
    return BenchResult(
        check_user_can_run=user_summary,
        check_agent_can_use_credential=credential_summary,
        cross_tenant_misgrant_allowed=f"{cross_allowed}/{len(bad)}",
        cross_tenant_allowed_count=cross_allowed,
        batchcheck_50=batch_summary,
        listobjects_can_run=list_run,
        listobjects_can_edit=list_edit,
        check_concurrency8=summ([elapsed for part in parts for elapsed in part]),
    )
