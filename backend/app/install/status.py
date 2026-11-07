"""The operator's system status: each component checked against the install profile.

A check reports what it found (``ok``, ``off``, ``error``); the profile turns that into what
it means here. A required component that is off or failing is an error, and a recommended
one is a warning. Database checks share the request's platform session, so they run one
after another. Network checks run concurrently, each with its own time limit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.guardrails import model_settings
from app.install.profiles import PROFILES, ComponentKey, Profile, ProfileKey, Requirement
from app.jobs import service as jobs
from app.mcp_runtime.probe import check_runtime
from app.models.job import JobWorker
from app.storage.object_store import get_object_store
from app.text import korean_terms

logger = logging.getLogger(__name__)

Found = Literal["ok", "off", "error"]
Verdict = Literal["ok", "warning", "error"]

CHECK_TIMEOUT_SECONDS: Final = 10.0
# A worker beats once per poll; three missed polls (and at least 30 s) mean it is gone.
_MIN_WORKER_SILENCE: Final = 30.0


@dataclass(frozen=True, slots=True)
class Check:
    found: Found
    detail: str


@dataclass(frozen=True, slots=True)
class ComponentStatus:
    key: ComponentKey
    requirement: Requirement
    found: Found
    verdict: Verdict
    detail: str


@dataclass(frozen=True, slots=True)
class SystemStatus:
    profile: ProfileKey
    verdict: Verdict
    components: list[ComponentStatus]
    workers: list[JobWorker]
    live_workers: int
    queue: jobs.QueueSummary
    checked_at: datetime = field(default_factory=jobs.now)


def verdict_for(requirement: Requirement, check: Check) -> Verdict:
    if check.found == "ok":
        return "ok"
    if requirement == "required":
        return "error"
    if requirement == "recommended":
        return "warning"
    # An optional component that is off is fine; one that is on but failing is not.
    return "warning" if check.found == "error" else "ok"


def worker_silence_seconds() -> float:
    return max(_MIN_WORKER_SILENCE, 3 * settings.jobs_poll_seconds)


async def _database(db: AsyncSession) -> Check:
    if db.get_bind().dialect.name != "postgresql":
        await db.execute(text("SELECT 1"))
        return Check("ok", db.get_bind().dialect.name)
    version = (await db.execute(text("SHOW server_version"))).scalar_one()
    return Check("ok", f"PostgreSQL {version}")


async def _pgvector(db: AsyncSession) -> Check:
    if db.get_bind().dialect.name != "postgresql":
        return Check("off", "needs PostgreSQL")
    installed = await db.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    version = installed.scalar_one_or_none()
    if version is not None:
        return Check("ok", f"vector {version}")
    offered = await db.execute(text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"))
    if offered.scalar_one_or_none() is not None:
        return Check("off", "offered by the server but not enabled; run the migrations")
    return Check("off", "not in this PostgreSQL image; use docker/postgres")


def _worker(rows: list[JobWorker], queue: jobs.QueueSummary) -> tuple[Check, int]:
    limit = worker_silence_seconds()
    live = sum(1 for row in rows if jobs.heartbeat_age(row).total_seconds() <= limit)
    if live == 0:
        return Check("off", f"no worker heartbeat in the last {int(limit)} s"), 0
    if queue.overdue:
        return Check("error", f"{live} live; {queue.overdue} jobs waiting over a minute"), live
    return Check("ok", f"{live} live"), live


async def _object_store(profile: Profile) -> Check:
    store = get_object_store()
    if profile.shared_storage and store.provider == "local":
        return Check("error", "this profile needs S3-compatible storage (STORAGE_BACKEND=s3)")
    health = await store.health()
    return Check("ok" if health.ok else "error", f"{health.provider}: {health.detail}")


async def _mcp_runtime() -> Check:
    health = await check_runtime()
    if not health.configured:
        return Check("off", health.detail)
    return Check("ok" if health.ok else "error", health.detail)


async def _pii_detector() -> Check:
    service = await model_settings.current()
    if not service.enabled:
        return Check("off", "no model service; the rules pack runs")
    result = await model_settings.probe(service.url, service.api_key)
    if result.status != "ok":
        return Check("error", result.status)
    return Check("ok", f"{result.model or 'model'} ({result.latency_ms} ms)")


async def _skill_sandbox() -> Check:
    if not settings.skill_sandbox_enabled:
        return Check("off", "skills run in the backend process")
    return Check("ok", "isolated runs on (not probed)")


async def _opensearch() -> Check:
    ok, detail = await korean_terms.probe()
    if not settings.opensearch_url:
        return Check("off", detail)
    return Check("ok" if ok else "error", detail)


async def _guarded(key: ComponentKey, check: Callable[[], Awaitable[Check]]) -> Check:
    try:
        return await asyncio.wait_for(check(), CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return Check("error", f"no answer in {int(CHECK_TIMEOUT_SECONDS)} s")
    except Exception as exc:  # noqa: BLE001 - one failing check must not hide the others
        logger.warning("system status check %s failed: %s", key, type(exc).__name__)
        return Check("error", type(exc).__name__)


async def collect(db: AsyncSession, *, profile_key: ProfileKey | None = None) -> SystemStatus:
    """Check every component against the profile (``db`` is a platform session)."""
    key = profile_key or settings.install_profile
    profile = PROFILES[key]
    found: dict[ComponentKey, Check] = {
        "database": await _guarded("database", lambda: _database(db)),
        "pgvector": await _guarded("pgvector", lambda: _pgvector(db)),
    }
    rows = await jobs.workers(db)
    queue = await jobs.queue_summary(db)
    found["worker"], live = _worker(rows, queue)
    network: dict[ComponentKey, Callable[[], Awaitable[Check]]] = {
        "object_store": lambda: _object_store(profile),
        "mcp_runtime": _mcp_runtime,
        "pii_detector": _pii_detector,
        "skill_sandbox": _skill_sandbox,
        "opensearch": _opensearch,
    }
    results = await asyncio.gather(*(_guarded(k, c) for k, c in network.items()))
    found.update(zip(network, results, strict=True))
    requirements = dict(profile.requirements)
    if settings.mcp_gateway_enabled:
        # D10: with the gateway on, every MCP call goes through the runtime.
        requirements["mcp_runtime"] = "required"
    components = [
        ComponentStatus(
            component,
            requirement,
            found[component].found,
            verdict_for(requirement, found[component]),
            found[component].detail,
        )
        for component, requirement in requirements.items()
    ]
    verdicts = {c.verdict for c in components}
    overall: Verdict = (
        "error" if "error" in verdicts else "warning" if "warning" in verdicts else "ok"
    )
    return SystemStatus(key, overall, components, rows, live, queue)
