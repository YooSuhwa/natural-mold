"""Which memory a run may read and write, by execution path (memory policy plan 15, 5.2).

A run's path is fixed by the entry that started its resume chain: an approval resume enters
through the chat routes but stays the trigger or API run it continues (``origin_run_path``).
The memory screens and the agent's settings narrow this further; nothing here widens them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run import ConversationRun

RunPath = Literal["chat", "trigger", "api", "channel", "subagent", "workflow"]


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
    # A workflow step reads the runner's memory for this agent and writes none: nobody
    # watches the run and every step would otherwise pile up proposals (workflow Q4).
    "workflow": MemoryPathAccess(False, True, False, False),
}
_CLOSED: Final = MemoryPathAccess(False, False, False, False)

# ``conversation_runs.run_source`` (principal vocabulary) → path.
_RUN_SOURCES: Final[dict[str, RunPath]] = {
    "CHAT": "chat",
    "TRIGGER": "trigger",
    "API": "api",
    "CHANNEL": "channel",
    "SUBAGENT": "subagent",
    "WORKFLOW": "workflow",
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


async def origin_run(db: AsyncSession, run: ConversationRun) -> ConversationRun | None:
    """The first run in ``run``'s resume chain (``parent_run_id`` links), or ``None`` when
    the chain cannot be followed to its start (a missing parent, a cycle, an overlong one)."""
    current = run
    seen: set[uuid.UUID] = set()
    while current.parent_run_id is not None:
        seen.add(current.id)
        if current.parent_run_id in seen or len(seen) >= _MAX_CHAIN:
            return None
        parent = (
            await db.execute(
                select(ConversationRun).where(ConversationRun.id == current.parent_run_id)
            )
        ).scalar_one_or_none()
        if parent is None:
            return None
        current = parent
    return current


async def origin_run_path(db: AsyncSession, run: ConversationRun) -> RunPath:
    """The path of the first run in ``run``'s resume chain.

    A broken chain reads as ``channel``: no personal memory rather than a guessed chat run.
    """
    first = await origin_run(db, run)
    if first is None:
        return "channel"
    return _RUN_SOURCES.get(first.run_source or "", "chat")


# A tool result that brings no outside text into the conversation (5.3-1).
INTERNAL_TOOLS: Final = frozenset(
    {
        "propose_memory",
        "save_user_memory",
        "save_agent_memory",
        "propose_agent_memo",
        "write_todos",
        "ask_user",
        "current_datetime",
    }
)
_SUMMARY_PREFIX: Final = "Here is a summary of the conversation to date"


def state_has_outside_content(messages: Iterable[Any]) -> bool:
    """Whether outside text reached the conversation (5.3-1, "대화 단위").

    A tool result other than the internal ones, a non-text attachment block in a user
    message, or a compaction summary (it may hide earlier tool results) all count.
    """
    for message in messages:
        kind = getattr(message, "type", None)
        if kind == "tool" and getattr(message, "name", None) not in INTERNAL_TOOLS:
            return True
        content = getattr(message, "content", None)
        if kind == "human":
            if isinstance(content, str) and content.startswith(_SUMMARY_PREFIX):
                return True
            if isinstance(content, list) and any(
                isinstance(block, Mapping) and block.get("type") not in (None, "text")
                for block in content
            ):
                return True
    return False


__all__ = [
    "INTERNAL_TOOLS",
    "MemoryPathAccess",
    "RunPath",
    "allowed_scopes_for",
    "memory_access",
    "origin_run",
    "origin_run_path",
    "state_has_outside_content",
]
