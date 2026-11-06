"""Built-in job types.

``system.probe`` does nothing but report back: enqueue it in a company to check end to end
that a worker picks jobs up and can write in that company's session.
"""

from __future__ import annotations

from typing import Any

from app.jobs import registry
from app.jobs.context import JobContext

PROBE = "system.probe"


async def _probe(context: JobContext) -> dict[str, Any]:
    await context.progress(50, "probe")
    context.check_canceled()
    return {"ok": True, "attempt": context.attempt}


def register_builtin() -> None:
    if registry.get(PROBE) is None:
        registry.register(registry.JobType(PROBE, _probe, lease_seconds=30, max_attempts=1))
