"""On-disk shared memo ``<data_root>/agents/<id>/AGENTS.md`` (plan 11.8; F-09).

Writes are staged: the new text goes to a temp file, the caller commits its database
changes, and only then is the temp file moved over the memo. A failed commit discards
the temp file and leaves the memo untouched. One host-level lock per memo serializes
read-modify-write (approve appends).
"""

from __future__ import annotations

import fcntl
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from app.agent_runtime.runtime_config import runtime_data_dir


def memo_path(agent_id: uuid.UUID) -> Path:
    return runtime_data_dir() / "agents" / str(agent_id) / "AGENTS.md"


def read_memo(agent_id: uuid.UUID) -> str:
    path = memo_path(agent_id)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


@contextmanager
def _locked(agent_id: uuid.UUID) -> Iterator[Path]:
    path = memo_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(".AGENTS.md.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


async def commit_then_write(
    agent_id: uuid.UUID,
    render: Callable[[str], str],
    commit: Callable[[], Awaitable[None]],
) -> str:
    """Under the memo lock: ``render(current)`` → temp file → ``commit()`` → replace."""
    with _locked(agent_id) as path:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        content = render(current)
        tmp = path.with_name(f".AGENTS.md.{uuid.uuid4().hex}.tmp")
        tmp.write_text(content, encoding="utf-8")
        try:
            await commit()
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(path)
        return content
