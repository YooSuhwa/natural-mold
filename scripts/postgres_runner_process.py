"""Owned subprocess process-group lifecycle for PostgreSQL test runs."""

from __future__ import annotations

import contextlib
import hashlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
_ACTIVE_PROCESS_GROUPS: dict[int, set[int]] = {}
_PROCESS_GROUP_FAILURES: set[int] = set()
_PROCESS_LOCK = threading.Lock()


def run_command(
    argv: list[str], *, env: dict[str, str] | None = None, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    """Run one owned child in its own process group and reap that group."""
    process = subprocess.Popen(  # noqa: S603 - internal fixed runner argv only
        argv,
        cwd=BACKEND_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    thread_id = threading.get_ident()
    with _PROCESS_LOCK:
        _ACTIVE_PROCESS_GROUPS.setdefault(thread_id, set()).add(process.pid)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        if not _process_group_absent(process.pid):
            with contextlib.suppress(OSError):
                _stop_process_group(process)
        if not _process_group_absent(process.pid):
            _record_process_group_failure(thread_id)
    except BaseException:
        with contextlib.suppress(BaseException):
            _stop_process_group(process)
        if not _process_group_absent(process.pid):
            _record_process_group_failure(thread_id)
        raise
    finally:
        with _PROCESS_LOCK:
            groups = _ACTIVE_PROCESS_GROUPS.get(thread_id, set())
            groups.discard(process.pid)
            if not groups:
                _ACTIVE_PROCESS_GROUPS.pop(thread_id, None)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _record_process_group_failure(thread_id: int) -> None:
    with _PROCESS_LOCK:
        _PROCESS_GROUP_FAILURES.add(thread_id)


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not _process_group_absent(process.pid):
        time.sleep(0.02)
    if not _process_group_absent(process.pid):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not _process_group_absent(process.pid):
            time.sleep(0.02)
    if process.poll() is None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=1)


def _process_group_absent(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def start_process_scope() -> None:
    """Reset process-group failure state for the calling runner scenario."""
    thread_id = threading.get_ident()
    with _PROCESS_LOCK:
        _PROCESS_GROUP_FAILURES.discard(thread_id)


def process_groups_stopped() -> bool:
    """Return whether every child process group for this scenario was reaped."""
    thread_id = threading.get_ident()
    with _PROCESS_LOCK:
        return (
            not _ACTIVE_PROCESS_GROUPS.get(thread_id) and thread_id not in _PROCESS_GROUP_FAILURES
        )


def process_identity_sha256() -> str:
    """Hash the non-secret parent process identity used by the cleanup receipt."""
    result = run_command(["ps", "-p", str(os.getpid()), "-o", "lstart=,command="], timeout=5)
    return hashlib.sha256(result.stdout.encode()).hexdigest()
