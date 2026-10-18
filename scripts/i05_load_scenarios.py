"""I-05 load scenarios (plan 16.6 load item, 8.10 targets) over real API/OpenFGA routes.

Targets with a number come from the plan: single Check p95 <= 20ms, BatchCheck 50 p95
<= 60ms, Check under 8 concurrent requests p95 <= 20ms, ListObjects within its 3s
default deadline. The plan only says "measure" for list APIs, run readiness, run start
and outbox propagation; those default to ``measured`` (no bound) except share
propagation, bounded by the 2s inline outbox write deadline (plan 8.6 "즉시 반영").
Every bound can be overridden from the config ``targets`` section.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from i05_http import Session, expect
from i05_stats import Target

PLAN = "plan 8.10/16.6"
LIST_PATHS = {
    "api.list.agents": "/api/agents",
    "api.list.skills": "/api/skills",
    "api.list.credentials": "/api/credentials",
    "api.list.mcp_servers": "/api/mcp-servers",
    "api.list.tools": "/api/tools",
    "api.list.marketplace": "/api/marketplace/items/page",
    "api.list.conversations": "/api/conversations/page",
}


@dataclass
class LoadContext:
    sessions: dict[str, Session]
    sections: dict[str, dict[str, Any]]
    fga: httpx.AsyncClient | None = None
    state: dict[str, Any] = field(default_factory=dict)

    def session(self, section: str) -> Session:
        return self.sessions[self.sections[section]["user"]]


Op = Callable[[LoadContext], Awaitable[dict[str, float] | None]]


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    targets: dict[str, Target]
    op: Op
    needs: tuple[str, ...]
    opt_in: bool = False
    concurrency: int | None = None


def _fga(ctx: LoadContext) -> httpx.AsyncClient:
    if ctx.fga is None:
        raise RuntimeError("OpenFGA client is not configured")
    return ctx.fga


def _fga_body(ctx: LoadContext, **extra: Any) -> dict[str, Any]:
    return {"authorization_model_id": ctx.sections["fga"]["model_id"], **extra}


def _store(ctx: LoadContext, suffix: str) -> str:
    return f"/stores/{ctx.sections['fga']['store_id']}/{suffix}"


async def fga_check(ctx: LoadContext) -> None:
    key = ctx.sections["fga"]["check"]
    body = _fga_body(ctx, tuple_key=key)
    reply = expect(await _fga(ctx).post(_store(ctx, "check"), json=body), 200).json()
    if not isinstance(reply.get("allowed"), bool):
        raise ValueError("Check reply without an allowed decision")


async def fga_batch_check(ctx: LoadContext) -> None:
    fga = ctx.sections["fga"]
    objects = list(fga["batch_objects"])
    checks = [
        {
            "tuple_key": {
                "user": fga["batch_user"],
                "relation": fga.get("batch_relation", "can_run"),
                "object": objects[index % len(objects)],
            },
            "correlation_id": str(index),
        }
        for index in range(50)
    ]
    reply = expect(
        await _fga(ctx).post(_store(ctx, "batch-check"), json=_fga_body(ctx, checks=checks)), 200
    ).json()
    if set(reply.get("result") or {}) != {str(index) for index in range(50)}:
        raise ValueError("BatchCheck reply does not answer all 50 correlation ids")


async def fga_list_objects(ctx: LoadContext) -> None:
    query = ctx.sections["fga"]["list_objects"]
    reply = expect(
        await _fga(ctx).post(_store(ctx, "list-objects"), json=_fga_body(ctx, **query)), 200
    ).json()
    if len(reply.get("objects") or []) >= 1000:
        raise ValueError("ListObjects reached the 1,000 object default limit")


async def resource_check(ctx: LoadContext) -> None:
    agent = ctx.sections["load"]["agent_id"]
    expect(await ctx.session("load").call("GET", f"/api/agents/{agent}"), 200)


async def run_readiness(ctx: LoadContext) -> None:
    agent = ctx.sections["load"]["agent_id"]
    path = f"/api/agents/{agent}/readiness"
    expect(await ctx.session("load").call("GET", path, params={"source": "CHAT"}), 200)


def list_op(path: str) -> Callable[[LoadContext], Awaitable[None]]:
    async def op(ctx: LoadContext) -> None:
        expect(await ctx.session("load").call("GET", path), 200)

    return op


async def run_start(ctx: LoadContext) -> dict[str, float]:
    session = ctx.session("run_start")
    agent = ctx.sections["run_start"]["agent_id"]
    created = expect(
        await session.call("POST", f"/api/agents/{agent}/conversations", json={"title": "i05"}),
        200,
        201,
    )
    conversation = created.json()["id"]
    command = {
        "id": 1,
        "method": "run.start",
        "params": {"input": {"messages": [{"role": "user", "content": "I-05 load probe"}]}},
    }
    path = f"/api/conversations/{conversation}/langgraph/threads/{conversation}/commands"
    started = time.perf_counter()
    reply = expect(await session.call("POST", path, json=command), 200)
    elapsed = (time.perf_counter() - started) * 1000
    run_id = reply.json()["result"]["run_id"]
    cancel = f"/api/conversations/{conversation}/runs/{run_id}/cancel"
    await session.call("POST", cancel)
    return {"api.run_start": elapsed}


async def _poll(session: Session, path: str, want: int, timeout_s: float, step_s: float) -> None:
    deadline = time.perf_counter() + timeout_s
    while True:
        response = await session.call("GET", path)
        if response.status_code == want:
            return
        if time.perf_counter() > deadline:
            raise TimeoutError(f"{path} still {response.status_code}, wanted {want}")
        await asyncio.sleep(step_s)


async def share_propagation(ctx: LoadContext) -> dict[str, float]:
    """Grant -> recipient sees 200; revoke -> recipient sees 404 (outbox -> OpenFGA)."""
    share = ctx.sections["share"]
    owner, recipient = ctx.sessions[share["owner"]], ctx.sessions[share["recipient"]]
    agent = share["agent_id"]
    timeout_s = float(share.get("timeout_s", 10))
    step_s = float(share.get("poll_interval_ms", 25)) / 1000
    probe = f"/api/agents/{agent}"
    grant = {"principal": {"type": "user", "id": recipient.user_id}, "level": "consumer"}
    started = time.perf_counter()
    created = await owner.call("POST", f"/api/resources/agent/{agent}/grants", json=grant)
    grant_id = expect(created, 201).json()["grant_id"]
    await _poll(recipient, probe, 200, timeout_s, step_s)
    granted = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    expect(await owner.call("DELETE", f"/api/grants/{grant_id}"), 200, 204)
    await _poll(recipient, probe, 404, timeout_s, step_s)
    revoked = (time.perf_counter() - started) * 1000
    return {"share.grant_visible": granted, "share.revoke_hidden": revoked}


def catalog() -> list[Scenario]:
    fga_needs = ("fga.url", "fga.store_id", "fga.model_id")
    check20 = {"fga.check": Target(p95_ms=20, source=PLAN)}
    scenarios = [
        Scenario("fga.check", check20, _none(fga_check), (*fga_needs, "fga.check")),
        Scenario(
            "fga.check_concurrent_8",
            {"fga.check_concurrent_8": Target(p95_ms=20, source=PLAN)},
            _none(fga_check),
            (*fga_needs, "fga.check"),
            concurrency=8,
        ),
        Scenario(
            "fga.batch_check_50",
            {"fga.batch_check_50": Target(p95_ms=60, source=PLAN)},
            _none(fga_batch_check),
            (*fga_needs, "fga.batch_user", "fga.batch_objects"),
        ),
        Scenario(
            "fga.list_objects",
            {"fga.list_objects": Target(p95_ms=3000, source="OpenFGA default 3s deadline")},
            _none(fga_list_objects),
            (*fga_needs, "fga.list_objects"),
        ),
        Scenario(
            "api.resource_check",
            {"api.resource_check": Target(source=PLAN)},
            _none(resource_check),
            ("load.user", "load.agent_id"),
        ),
        Scenario(
            "api.run_readiness",
            {"api.run_readiness": Target(source=PLAN)},
            _none(run_readiness),
            ("load.user", "load.agent_id"),
        ),
    ]
    scenarios += [
        Scenario(name, {name: Target(source=PLAN)}, _none(list_op(path)), ("load.user",))
        for name, path in LIST_PATHS.items()
    ]
    outbox = Target(p95_ms=2000, source="plan 8.6 inline outbox write 2s")
    scenarios += [
        Scenario(
            "api.run_start",
            {"api.run_start": Target(source=PLAN)},
            run_start,
            ("run_start.user", "run_start.agent_id"),
            opt_in=True,
        ),
        Scenario(
            "share.propagation",
            {"share.grant_visible": outbox, "share.revoke_hidden": outbox},
            share_propagation,
            ("share.owner", "share.recipient", "share.agent_id"),
            concurrency=1,
        ),
    ]
    return scenarios


def _none(op: Callable[[LoadContext], Awaitable[None]]) -> Op:
    async def wrapped(ctx: LoadContext) -> dict[str, float] | None:
        await op(ctx)
        return None

    return wrapped
