"""Which memory a run may read and write, by execution path (memory policy plan 15, 5.2).

A run's path is fixed by the entry that started its resume chain: an approval resume enters
through the chat routes but stays the trigger or API run it continues (``origin_run_path``).
The memory screens and the agent's settings narrow this further; nothing here widens them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run import ConversationRun

RunPath = Literal["chat", "trigger", "api", "channel", "subagent"]


@dataclass(frozen=True, slots=True)
class MemoryPathAccess:
    user_scope: bool  # the runner's personal global memory
    agent_scope: bool  # the runner's memory for this agent
    write_tools: bool  # memory write tools may be attached
    memo_mount: bool  # legacy mode: AGENTS.md mounted as a writable memory source


_ACCESS: Final[dict[str, MemoryPathAccess]] = {
    "chat": MemoryPathAccess(True, True, True, True),
    # The trigger owner's agent memory; personal global memory never reaches a schedule.
    "trigger": MemoryPathAccess(False, True, True, False),
    # An external caller, a channel or a child agent sees no personal memory and writes none.
    "api": MemoryPathAccess(False, False, False, False),
    "channel": MemoryPathAccess(False, False, False, False),
    "subagent": MemoryPathAccess(False, False, False, False),
}
_CLOSED: Final = MemoryPathAccess(False, False, False, False)

# ``conversation_runs.run_source`` (principal vocabulary) → path.
_RUN_SOURCES: Final[dict[str, RunPath]] = {
    "CHAT": "chat",
    "TRIGGER": "trigger",
    "API": "api",
    "CHANNEL": "channel",
    "SUBAGENT": "subagent",
}
_MAX_CHAIN: Final = 64


def memory_access(path: str) -> MemoryPathAccess:
    """Unknown paths get nothing (fail closed)."""
    return _ACCESS.get(path, _CLOSED)


def allowed_scopes_for(path: str, allowed: str) -> str | None:
    """Intersect the settings' ``allowed_scopes`` with the path; ``None`` = no record read."""
    access = memory_access(path)
    user = access.user_scope and allowed in ("user", "both")
    agent = access.agent_scope and allowed in ("agent", "both")
    if user and agent:
        return "both"
    return "user" if user else "agent" if agent else None


async def origin_run_path(db: AsyncSession, run: ConversationRun) -> RunPath:
    """The path of the first run in ``run``'s resume chain (``parent_run_id`` links).

    A chain that cannot be followed to its start (a missing parent, a cycle, an overlong
    chain) reads as ``channel``: no personal memory rather than a guessed chat run.
    """
    current = run
    seen: set[uuid.UUID] = set()
    while current.parent_run_id is not None:
        seen.add(current.id)
        if current.parent_run_id in seen or len(seen) >= _MAX_CHAIN:
            return "channel"
        parent = (
            await db.execute(
                select(ConversationRun).where(ConversationRun.id == current.parent_run_id)
            )
        ).scalar_one_or_none()
        if parent is None:
            return "channel"
        current = parent
    return _RUN_SOURCES.get(current.run_source or "", "chat")


__all__ = [
    "MemoryPathAccess",
    "RunPath",
    "allowed_scopes_for",
    "memory_access",
    "origin_run_path",
]
