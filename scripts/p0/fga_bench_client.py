"""Bounded OpenFGA requests with required authorization decision parsing."""

from __future__ import annotations

import time
from pathlib import Path

import anyio
import httpx
from fga_bench_data import TupleKey, tk, tuples
from fga_bench_types import (
    BatchReply,
    CheckReply,
    LoadedStore,
    ModelReply,
    ObjectsReply,
    StoreJournal,
    StoreReply,
)


def require_status(response: httpx.Response, expected: int = 200) -> None:
    if response.status_code != expected:
        message = f"OpenFGA returned {response.status_code}: {response.text[:300]}"
        raise httpx.HTTPStatusError(message, request=response.request, response=response)


async def setup(
    client: httpx.AsyncClient,
    name: str,
    model_file: Path,
    current: bool,
    store_ids: list[str],
    journal_path: Path,
) -> LoadedStore:
    response = await client.post("/stores", json={"name": name})
    require_status(response, 201)
    store_id = StoreReply.model_validate_json(response.content).id
    store_ids.append(store_id)
    await anyio.Path(journal_path).write_text(
        StoreJournal(store_ids=store_ids).model_dump_json(indent=2),
        encoding="utf-8",
    )
    print(f"created store {store_id} ({name})", flush=True)
    model_bytes = await anyio.Path(model_file).read_bytes()
    response = await client.post(
        f"/stores/{store_id}/authorization-models",
        content=model_bytes,
        headers={"content-type": "application/json"},
    )
    require_status(response, 201)
    model_id = ModelReply.model_validate_json(response.content).authorization_model_id
    generated, bad = tuples(current)
    limiter = anyio.CapacityLimiter(16)

    async def write(chunk: list[TupleKey]) -> None:
        async with limiter:
            response = await client.post(
                f"/stores/{store_id}/write",
                json={"writes": {"tuple_keys": chunk}, "authorization_model_id": model_id},
            )
            require_status(response)

    started = time.perf_counter()
    async with anyio.create_task_group() as tasks:
        for index in range(0, len(generated), 100):
            tasks.start_soon(write, generated[index : index + 100])
    print(
        f"[{name}] tuples={len(generated)} write={time.perf_counter() - started:.1f}s", flush=True
    )
    return LoadedStore(store_id, model_id, bad, len(generated))


async def check(
    client: httpx.AsyncClient,
    store_id: str,
    model_id: str,
    user: str,
    relation: str,
    target: str,
) -> tuple[float, bool]:
    started = time.perf_counter()
    response = await client.post(
        f"/stores/{store_id}/check",
        json={"tuple_key": tk(user, relation, target), "authorization_model_id": model_id},
    )
    elapsed = (time.perf_counter() - started) * 1000
    require_status(response)
    return elapsed, CheckReply.model_validate_json(response.content).allowed


async def batch_check(
    client: httpx.AsyncClient,
    store_id: str,
    model_id: str,
    user: str,
    targets: list[str],
) -> float:
    checks = [
        {"tuple_key": tk(user, "can_run", f"agent:{target}"), "correlation_id": str(index)}
        for index, target in enumerate(targets)
    ]
    started = time.perf_counter()
    response = await client.post(
        f"/stores/{store_id}/batch-check",
        json={"checks": checks, "authorization_model_id": model_id},
    )
    elapsed = (time.perf_counter() - started) * 1000
    require_status(response)
    parsed = BatchReply.model_validate_json(response.content)
    if set(parsed.result) != {str(index) for index in range(len(targets))}:
        message = "BatchCheck response does not contain exactly the requested correlation IDs"
        raise httpx.DecodingError(message, request=response.request)
    return elapsed


async def list_objects(
    client: httpx.AsyncClient,
    store_id: str,
    model_id: str,
    user: str,
    relation: str,
) -> tuple[float, int]:
    started = time.perf_counter()
    response = await client.post(
        f"/stores/{store_id}/list-objects",
        json={
            "user": user,
            "relation": relation,
            "type": "agent",
            "authorization_model_id": model_id,
        },
    )
    elapsed = (time.perf_counter() - started) * 1000
    require_status(response)
    return elapsed, len(ObjectsReply.model_validate_json(response.content).objects)
